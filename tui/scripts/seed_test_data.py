#!/usr/bin/env python
"""Seed a disposable klawde SQLite DB with fake sessions for TUI testing.

Writes to /tmp/klawde-test/sessions.db so the real ~/.klawde/sessions.db
stays untouched. Point the TUI at the seed DB by exporting KLAWDE_DB
before launching klawde.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEED_DIR = Path("/tmp/klawde-test")
SEED_DB = SEED_DIR / "sessions.db"

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
    exceeds_200k_tokens INTEGER,
    output_style TEXT,
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


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    SEED_DB.unlink(missing_ok=True)

    conn = sqlite3.connect(SEED_DB)
    conn.executescript(_DDL)

    sessions = [
        # (sid, cwd, status, model, ctx_pct, started_offset_min, kitty_wid)
        ("aaaaaaaa1111", "/home/user/alpha-project", "running",         "claude-opus-4-7",   12,  5,  3),
        ("bbbbbbbb2222", "/home/user/beta-service",  "needs_approval",  "claude-sonnet-4-6", 42, 20,  7),
        ("cccccccc3333", "/var/log",                 "running",         "claude-haiku-4-5",   8, 75, None),
        ("dddddddd4444", "/tmp/ghost",               "running",         "claude-opus-4-7",   67, 30, 99),
    ]

    for sid, cwd, status, model, ctx, off, kw in sessions:
        ts = _ts(off)
        conn.execute(
            """INSERT INTO sessions(session_id, cwd, status, model, context_percent,
                                    started_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (sid, cwd, status, model, ctx, ts, ts),
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
