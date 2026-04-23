#!/usr/bin/env python
"""Seed a disposable klawde SQLite DB with fake sessions for TUI testing.

Writes to /tmp/klawde-test/sessions.db so the real ~/.klawde/sessions.db
stays untouched. Point the TUI at the seed DB by exporting KLAWDE_DB
before launching klawde.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEED_DIR = Path("/tmp/klawde-test")
SEED_DB = SEED_DIR / "sessions.db"
FIXTURE_DIR = SEED_DIR / "fixtures"

# Inlined DDL — mirrors metrics/setup.sh. Keep in sync when the schema changes.
_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
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
    git_branch TEXT,
    exceeds_200k_tokens INTEGER,
    output_style TEXT,
    prev_cost_usd REAL,
    prev_cost_sampled_at TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT
);
CREATE TABLE IF NOT EXISTS session_metadata (
    session_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, namespace, key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""


def _ts(offset_min: int) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=offset_min))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _future_ts(offset_min: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(minutes=offset_min))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    SEED_DB.unlink(missing_ok=True)

    conn = sqlite3.connect(SEED_DB)
    conn.executescript(_DDL)

    rl_5h_resets = _future_ts(180)      # 3 hours from now
    rl_7d_resets = _future_ts(60 * 48)  # 2 days from now

    # Write transcript fixtures. One with a pending Bash tool, one with all
    # tool calls resolved (drives the "no pending → reset to running" path).
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    pending_fixture = FIXTURE_DIR / "bbbbbbbb2222.jsonl"
    with pending_fixture.open("w") as f:
        f.write(json.dumps({
            "type": "user",
            "uuid": "u-0",
            "message": {"role": "user", "content": [
                {"type": "text", "text": "clean up the npm dependencies, my node_modules directory is huge"},
            ]},
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "uuid": "a-1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_seed1", "name": "Bash",
                 "input": {"command": "rm -rf node_modules",
                           "description": "Clean npm deps"}},
            ]},
        }) + "\n")
    stale_fixture = FIXTURE_DIR / "eeeeeeee5555.jsonl"
    with stale_fixture.open("w") as f:
        # tool_use immediately resolved — no pending tool → reset path.
        f.write(json.dumps({
            "type": "assistant",
            "uuid": "a-1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_seed2", "name": "Read",
                 "input": {"file_path": "/etc/hosts"}},
            ]},
        }) + "\n")
        f.write(json.dumps({
            "type": "user",
            "uuid": "u-1",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_seed2",
                 "content": "127.0.0.1 localhost"},
            ]},
        }) + "\n")

    # AskUserQuestion fixture — exercises the specialized questions renderer.
    auq_fixture = FIXTURE_DIR / "ffffffff6666.jsonl"
    with auq_fixture.open("w") as f:
        f.write(json.dumps({
            "type": "user",
            "uuid": "u-0",
            "message": {"role": "user", "content": [
                {"type": "text", "text": "help me pick a fib(n) implementation"},
            ]},
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "uuid": "a-1",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_seed3", "name": "AskUserQuestion",
                 "input": {"questions": [{
                     "question": "Which fib(n) implementation?",
                     "header": "Fib impl",
                     "multiSelect": False,
                     "options": [
                         {"label": "Iterative loop",
                          "description": "O(n) time, O(1) space."},
                         {"label": "Naive recursion",
                          "description": "O(2^n). Teaching only."},
                         {"label": "Memoized recursion",
                          "description": "O(n) time + space. lru_cache."},
                         {"label": "Matrix exponentiation",
                          "description": "O(log n). Overkill unless huge n."},
                     ],
                 }]}},
            ]},
        }) + "\n")

    sessions = [
        # (sid, cwd, status, model, ctx_pct, started_offset_min, kitty_wid, cost, prev_cost, prev_sampled_ago_min, transcript_path, branch)
        ("aaaaaaaa1111", "/home/user/alpha-project", "running",         "claude-opus-4-7",   12,  5,  3,  4.80,  4.40, 2,    None,                  "main"),
        ("bbbbbbbb2222", "/home/user/beta-service",  "needs_approval",  "claude-sonnet-4-6", 42, 20,  7,  0.03,  0.02, 3,    str(pending_fixture),  "feat/cleanup-deps"),
        ("cccccccc3333", "/var/log",                 "running",         "claude-haiku-4-5",   8, 75, None, None, None, None, None,                  None),
        ("dddddddd4444", "/tmp/ghost",               "running",         "claude-opus-4-7",   67, 30, 99, 27.40, 27.10, 1,    None,                  "a1b2c3d"),
        ("eeeeeeee5555", "/home/user/gamma-tool",    "needs_approval",  "claude-opus-4-7",   30, 10, 11, 0.50,  0.45,  2,    str(stale_fixture),    "release/2.4"),
        ("ffffffff6666", "/home/user/delta-app",     "needs_approval",  "claude-opus-4-7",   22, 15, 13, 1.25,  1.20,  2,    str(auq_fixture),      "phansen/really-long-branch-name-for-truncation"),
    ]

    # Rate limits are account-level; attach to whichever session ticks last.
    # The TUI picks the row with the newest updated_at, so put RL values only
    # on the most-recently-updated seed session (offset 5m → alpha-project).
    for sid, cwd, status, model, ctx, off, kw, cost, prev_cost, prev_ago, tpath, branch in sessions:
        ts = _ts(off)
        is_latest = off == min(s[5] for s in sessions)
        rl_5h_pct = 45.0 if is_latest else None
        rl_7d_pct = 12.0 if is_latest else None
        prev_sampled_at = _ts(prev_ago) if prev_ago is not None else None
        conn.execute(
            """INSERT INTO sessions(session_id, cwd, status, model, context_percent,
                                    started_at, updated_at, total_cost_usd,
                                    prev_cost_usd, prev_cost_sampled_at,
                                    rate_limit_5h_percent, rate_limit_5h_resets_at,
                                    rate_limit_7d_percent, rate_limit_7d_resets_at,
                                    transcript_path, git_branch)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, cwd, status, model, ctx, ts, ts, cost,
             prev_cost, prev_sampled_at,
             rl_5h_pct, rl_5h_resets if rl_5h_pct is not None else None,
             rl_7d_pct, rl_7d_resets if rl_7d_pct is not None else None,
             tpath, branch),
        )
        if kw is not None:
            conn.execute(
                """INSERT INTO session_metadata(session_id, namespace, key, value, updated_at)
                   VALUES(?, 'kitty', 'window_id', ?, ?)""",
                (sid, str(kw), ts),
            )
            conn.execute(
                """INSERT INTO session_metadata(session_id, namespace, key, value, updated_at)
                   VALUES(?, 'kitty', 'listen_on', ?, ?)""",
                (sid, "unix:/tmp/kitty-seed", ts),
            )

    conn.commit()
    conn.close()

    print(f"Seeded {len(sessions)} sessions into {SEED_DB}")
    print()
    print("Run the TUI with:")
    print(f"  export KLAWDE_DB={SEED_DB}")
    print("  uv run klawde")


if __name__ == "__main__":
    main()
