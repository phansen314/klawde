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
