#!/usr/bin/env python
"""Seed fake session events to /tmp/klawde-test/session-events.jsonl for TUI testing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEED_DIR = Path("/tmp/klawde-test")
SEED_FILE = SEED_DIR / "session-events.jsonl"


def _ts(offset_min: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=offset_min)).isoformat().replace("+00:00", "Z")


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    events = [
        {"event": "start", "session_id": "aaaaaaaa1111", "cwd": "/home/user/alpha-project", "kitty_window_id": 3, "timestamp": _ts(5)},
        {"event": "start", "session_id": "bbbbbbbb2222", "cwd": "/home/user/beta-service", "kitty_window_id": 7, "timestamp": _ts(20)},
        {"event": "start", "session_id": "cccccccc3333", "cwd": "/var/log", "kitty_window_id": None, "timestamp": _ts(75)},
        {"event": "start", "session_id": "dddddddd4444", "cwd": "/tmp/ghost", "kitty_window_id": 99, "timestamp": _ts(30)},
        {"event": "stop", "session_id": "dddddddd4444", "timestamp": _ts(1)},
    ]

    with SEED_FILE.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    print(f"Wrote {len(events)} events to {SEED_FILE}")
    print()
    print("Run the TUI with:")
    print(f"  export CLAUDE_SESSION_EVENTS={SEED_FILE}")
    print("  klawde")


if __name__ == "__main__":
    main()
