from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from klawde.tui import SessionApp


def _ts(mins_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=mins_ago)).isoformat().replace("+00:00", "Z")


def test_tail_only_reads_end_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = tmp_path / "events.jsonl"
    with events.open("w") as f:
        for i in range(2000):
            f.write(
                json.dumps({
                    "event": "start",
                    "session_id": f"old{i:04d}",
                    "cwd": "/old",
                    "kitty_window_id": None,
                    "timestamp": _ts(500 + i),
                })
                + "\n"
            )
        for i in range(3):
            f.write(
                json.dumps({
                    "event": "start",
                    "session_id": f"new{i}",
                    "cwd": "/new",
                    "kitty_window_id": i + 1,
                    "timestamp": _ts(5 - i),
                })
                + "\n"
            )

    assert events.stat().st_size > 250_000
    monkeypatch.setenv("CLAUDE_SESSION_EVENTS", str(events))

    app = SessionApp()
    app._read_new_events()

    for sid in ("new0", "new1", "new2"):
        assert sid in app.sessions
    assert "old0000" not in app.sessions
    assert app._file_offset == events.stat().st_size
