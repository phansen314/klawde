from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from klawde.db import SessionRepo

# Mirrors metrics/setup.sh — intentionally inlined so the test is self-contained
# and doesn't shell out. Keep in sync when schema changes.
_DDL = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    transcript_path TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    model TEXT,
    session_name TEXT,
    context_percent INTEGER,
    context_window_size INTEGER,
    rate_limit_5h_percent REAL,
    rate_limit_5h_resets_at TEXT,
    rate_limit_7d_percent REAL,
    rate_limit_7d_resets_at TEXT,
    total_cost_usd REAL,
    original_cwd TEXT,
    api_duration_ms INTEGER,
    lines_added INTEGER,
    lines_removed INTEGER,
    total_input_tokens INTEGER,
    total_output_tokens INTEGER,
    claude_code_version TEXT,
    git_worktree TEXT,
    exceeds_200k_tokens INTEGER,
    output_style TEXT,
    prev_cost_usd REAL,
    prev_cost_sampled_at TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT
);
CREATE TABLE session_metadata (
    session_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, namespace, key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return path


def _insert_session(
    db: Path,
    sid: str,
    *,
    cwd: str = "/tmp",
    status: str = "running",
    model: str | None = "claude-opus-4-7",
    context_percent: int | None = 12,
    started_at: str = "2026-04-20T10:00:00.000Z",
) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sessions(session_id, cwd, status, model, context_percent, started_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (sid, cwd, status, model, context_percent, started_at, started_at),
    )
    conn.commit()
    conn.close()


def _insert_kitty_meta(db: Path, sid: str, window_id: int, listen_on: str = "unix:/tmp/k") -> None:
    conn = sqlite3.connect(db)
    ts = "2026-04-20T10:00:00.000Z"
    conn.execute(
        "INSERT INTO session_metadata(session_id, namespace, key, value, updated_at) "
        "VALUES(?, 'kitty', 'window_id', ?, ?)",
        (sid, str(window_id), ts),
    )
    conn.execute(
        "INSERT INTO session_metadata(session_id, namespace, key, value, updated_at) "
        "VALUES(?, 'kitty', 'listen_on', ?, ?)",
        (sid, listen_on, ts),
    )
    conn.commit()
    conn.close()


def test_empty_db_returns_empty_list(db: Path) -> None:
    repo = SessionRepo(db)
    assert repo.list_sessions() == []


def test_missing_db_file_returns_empty_list(tmp_path: Path) -> None:
    # No DB at all — read-only URI mode raises; repo catches and returns [].
    repo = SessionRepo(tmp_path / "does-not-exist.db")
    assert repo.list_sessions() == []


def test_running_session_without_kitty_metadata(db: Path) -> None:
    _insert_session(db, "aaaa1111-0000-0000-0000-000000000001")
    sessions = SessionRepo(db).list_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == "aaaa1111-0000-0000-0000-000000000001"
    assert s.kitty_window_id is None
    assert s.status == "running"
    assert s.model == "claude-opus-4-7"
    assert s.context_percent == 12


def test_session_with_kitty_metadata_populates_window_id(db: Path) -> None:
    _insert_session(db, "aaaa1111-0000-0000-0000-000000000002")
    _insert_kitty_meta(db, "aaaa1111-0000-0000-0000-000000000002", 42)
    sessions = SessionRepo(db).list_sessions()
    assert sessions[0].kitty_window_id == 42


def test_stopped_sessions_are_excluded(db: Path) -> None:
    _insert_session(db, "running-0000-0000-0000-000000000000", status="running")
    _insert_session(db, "stopped-0000-0000-0000-000000000000", status="stopped")
    sessions = SessionRepo(db).list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id.startswith("running")


def test_needs_approval_sorts_first(db: Path) -> None:
    # Insert running BEFORE needs_approval (older started_at) so time-ordering
    # alone would put running first. needs_approval must override.
    _insert_session(
        db, "older-run-0000-0000-0000-000000000000",
        status="running", started_at="2026-04-20T09:00:00.000Z",
    )
    _insert_session(
        db, "newer-app-0000-0000-0000-000000000000",
        status="needs_approval", started_at="2026-04-20T10:00:00.000Z",
    )
    _insert_session(
        db, "newest-ru-0000-0000-0000-000000000000",
        status="running", started_at="2026-04-20T11:00:00.000Z",
    )
    sessions = SessionRepo(db).list_sessions()
    assert [s.session_id[:9] for s in sessions] == [
        "newer-app",   # needs_approval — first despite not being newest
        "newest-ru",   # running, newer
        "older-run",   # running, older
    ]


def test_multiple_running_sessions_ordered_by_started_at_desc(db: Path) -> None:
    _insert_session(db, "sid-older", started_at="2026-04-20T09:00:00.000Z")
    _insert_session(db, "sid-mid",   started_at="2026-04-20T10:00:00.000Z")
    _insert_session(db, "sid-new",   started_at="2026-04-20T11:00:00.000Z")
    sessions = SessionRepo(db).list_sessions()
    assert [s.session_id for s in sessions] == ["sid-new", "sid-mid", "sid-older"]


def test_started_at_parsed_to_datetime(db: Path) -> None:
    _insert_session(db, "ts-0001", started_at="2026-04-20T10:00:00.000Z")
    s = SessionRepo(db).list_sessions()[0]
    assert s.started_at.year == 2026
    assert s.started_at.tzinfo is not None


def test_session_exposes_total_cost_usd(db: Path) -> None:
    sid = "cost-0001-0000-0000-0000-000000000000"
    _insert_session(db, sid)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET total_cost_usd = 4.80 WHERE session_id = ?", (sid,))
    conn.commit()
    conn.close()
    sessions = SessionRepo(db).list_sessions()
    assert sessions[0].total_cost_usd == 4.80


def test_total_cost_usd_null_when_missing(db: Path) -> None:
    _insert_session(db, "cost-null-0000-0000-0000-000000000000")
    sessions = SessionRepo(db).list_sessions()
    assert sessions[0].total_cost_usd is None


def _insert_rl(
    db: Path,
    sid: str,
    *,
    five_h_pct: float | None,
    five_h_resets: str | None,
    seven_d_pct: float | None,
    seven_d_resets: str | None,
    updated_at: str,
) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        """UPDATE sessions SET
             rate_limit_5h_percent = ?, rate_limit_5h_resets_at = ?,
             rate_limit_7d_percent = ?, rate_limit_7d_resets_at = ?,
             updated_at = ?
           WHERE session_id = ?""",
        (five_h_pct, five_h_resets, seven_d_pct, seven_d_resets, updated_at, sid),
    )
    conn.commit()
    conn.close()


def test_get_rate_limits_returns_none_when_no_data(db: Path) -> None:
    _insert_session(db, "rl-none-0000-0000-0000-000000000000")
    assert SessionRepo(db).get_rate_limits() is None


def test_get_rate_limits_picks_latest_updated_session(db: Path) -> None:
    # Older session: 5h=10%, 7d=5%.
    _insert_session(db, "rl-older-0000-0000-0000-000000000000")
    _insert_rl(
        db, "rl-older-0000-0000-0000-000000000000",
        five_h_pct=10.0, five_h_resets="2026-04-21T23:00:00Z",
        seven_d_pct=5.0,  seven_d_resets="2026-04-23T18:00:00Z",
        updated_at="2026-04-20T10:00:00.000Z",
    )
    # Newer session: 5h=45%, 7d=12%. Must win.
    _insert_session(db, "rl-newer-0000-0000-0000-000000000000")
    _insert_rl(
        db, "rl-newer-0000-0000-0000-000000000000",
        five_h_pct=45.0, five_h_resets="2026-04-22T01:00:00Z",
        seven_d_pct=12.0, seven_d_resets="2026-04-24T18:00:00Z",
        updated_at="2026-04-20T11:00:00.000Z",
    )
    rl = SessionRepo(db).get_rate_limits()
    assert rl is not None
    assert rl.five_hour_pct == 45.0
    assert rl.seven_day_pct == 12.0
    assert rl.five_hour_resets_at is not None
    assert rl.five_hour_resets_at.year == 2026
    assert rl.seven_day_resets_at is not None


def test_get_rate_limits_ignores_stopped_sessions(db: Path) -> None:
    # A stopped session with the newest updated_at must NOT win. Its RL
    # snapshot is stale and should not leak into the TUI's top panel.
    _insert_session(db, "rl-stopped-0000-0000-0000-000000000000", status="stopped")
    _insert_rl(
        db, "rl-stopped-0000-0000-0000-000000000000",
        five_h_pct=90.0, five_h_resets="2026-04-22T23:00:00Z",
        seven_d_pct=75.0, seven_d_resets="2026-04-25T18:00:00Z",
        updated_at="2026-04-21T12:00:00.000Z",  # newest
    )
    _insert_session(db, "rl-live-0000-0000-0000-000000000000", status="running")
    _insert_rl(
        db, "rl-live-0000-0000-0000-000000000000",
        five_h_pct=20.0, five_h_resets="2026-04-22T01:00:00Z",
        seven_d_pct=8.0,  seven_d_resets="2026-04-24T18:00:00Z",
        updated_at="2026-04-21T10:00:00.000Z",  # older but live
    )
    rl = SessionRepo(db).get_rate_limits()
    assert rl is not None
    assert rl.five_hour_pct == 20.0  # from the live row, not the stale stopped one


def test_get_rate_limits_handles_partial_data(db: Path) -> None:
    # Only 5h populated — 7d NULL. Should still return a RateLimits row.
    _insert_session(db, "rl-partial-0000-0000-0000-000000000000")
    _insert_rl(
        db, "rl-partial-0000-0000-0000-000000000000",
        five_h_pct=30.0, five_h_resets="2026-04-21T23:00:00Z",
        seven_d_pct=None, seven_d_resets=None,
        updated_at="2026-04-20T11:00:00.000Z",
    )
    rl = SessionRepo(db).get_rate_limits()
    assert rl is not None
    assert rl.five_hour_pct == 30.0
    assert rl.seven_day_pct is None
    assert rl.seven_day_resets_at is None


def test_session_exposes_updated_at(db: Path) -> None:
    _insert_session(db, "upd-0001-0000-0000-0000-000000000000")
    s = SessionRepo(db).list_sessions()[0]
    assert s.updated_at.year == 2026
    assert s.updated_at.tzinfo is not None


def _set_burn(db: Path, sid: str, *, prev_cost: float, prev_sampled_ago_sec: int,
              total_cost: float) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        """UPDATE sessions
              SET prev_cost_usd = ?,
                  prev_cost_sampled_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ? || ' seconds'),
                  total_cost_usd = ?
            WHERE session_id = ?""",
        (prev_cost, -prev_sampled_ago_sec, total_cost, sid),
    )
    conn.commit()
    conn.close()


def test_get_burn_rate_none_when_no_samples(db: Path) -> None:
    _insert_session(db, "burn-empty-0000-0000-0000-000000000000")
    assert SessionRepo(db).get_burn_rate_per_hr() is None


def test_get_burn_rate_single_session(db: Path) -> None:
    # $0.50 spent over 60s → $30/hr.
    sid = "burn-single-0000-0000-0000-000000000000"
    _insert_session(db, sid)
    _set_burn(db, sid, prev_cost=1.00, prev_sampled_ago_sec=60, total_cost=1.50)
    rate = SessionRepo(db).get_burn_rate_per_hr()
    assert rate is not None
    assert 29 < rate < 31


def test_get_burn_rate_sums_live_sessions(db: Path) -> None:
    # Session A: $0.50 over 60s = $30/hr.
    _insert_session(db, "burn-a-0000-0000-0000-000000000000", status="running")
    _set_burn(db, "burn-a-0000-0000-0000-000000000000",
              prev_cost=1.00, prev_sampled_ago_sec=60, total_cost=1.50)
    # Session B: $0.10 over 60s = $6/hr.
    _insert_session(db, "burn-b-0000-0000-0000-000000000000", status="running")
    _set_burn(db, "burn-b-0000-0000-0000-000000000000",
              prev_cost=0.20, prev_sampled_ago_sec=60, total_cost=0.30)
    # Stopped session: must be excluded.
    _insert_session(db, "burn-c-0000-0000-0000-000000000000", status="stopped")
    _set_burn(db, "burn-c-0000-0000-0000-000000000000",
              prev_cost=5.00, prev_sampled_ago_sec=60, total_cost=10.00)
    rate = SessionRepo(db).get_burn_rate_per_hr()
    assert rate is not None
    assert 35 < rate < 37


def test_get_burn_rate_skips_partial_rows(db: Path) -> None:
    # Has total_cost but never sampled → excluded. No NULL arithmetic crashes.
    sid = "burn-partial-0000-0000-0000-000000000000"
    _insert_session(db, sid)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET total_cost_usd = 1.00 WHERE session_id = ?", (sid,))
    conn.commit()
    conn.close()
    assert SessionRepo(db).get_burn_rate_per_hr() is None


def test_session_exposes_transcript_path(db: Path) -> None:
    sid = "tx-0001-0000-0000-0000-000000000000"
    _insert_session(db, sid)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE sessions SET transcript_path = ? WHERE session_id = ?",
        ("/tmp/fixtures/t.jsonl", sid),
    )
    conn.commit()
    conn.close()
    s = SessionRepo(db).list_sessions()[0]
    assert s.transcript_path == "/tmp/fixtures/t.jsonl"


def test_transcript_path_null_when_missing(db: Path) -> None:
    _insert_session(db, "tx-null-0000-0000-0000-000000000000")
    s = SessionRepo(db).list_sessions()[0]
    assert s.transcript_path is None


def test_reset_needs_approval_flips_status(db: Path, tmp_path: Path, monkeypatch) -> None:
    sid = "rna-0001-0000-0000-0000-000000000000"
    _insert_session(db, sid, status="needs_approval")
    # Point the flag dir somewhere writable and create a stub flag file.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    flag = tmp_path / f"klawde-needs-approval-{sid}"
    flag.write_text("")
    assert flag.exists()

    repo = SessionRepo(db)
    changed = repo.reset_needs_approval(sid)
    assert changed is True

    s = repo.list_sessions()[0]
    assert s.status == "running"
    assert not flag.exists()


def test_reset_needs_approval_noop_on_running_row(db: Path) -> None:
    sid = "rna-0002-0000-0000-0000-000000000000"
    _insert_session(db, sid, status="running")
    repo = SessionRepo(db)
    assert repo.reset_needs_approval(sid) is False


def test_reset_needs_approval_noop_on_unknown_session(db: Path) -> None:
    repo = SessionRepo(db)
    assert repo.reset_needs_approval("does-not-exist") is False


def test_env_var_overrides_default_path(tmp_path: Path, monkeypatch) -> None:
    # KLAWDE_DB points SessionRepo at the seed DB when no explicit path passed.
    db_path = tmp_path / "env.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("KLAWDE_DB", str(db_path))
    repo = SessionRepo()
    assert repo.db_path == db_path
    assert repo.list_sessions() == []
