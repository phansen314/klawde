from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from klawde.tui import SessionApp


def _ts(mins_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=mins_ago)).isoformat().replace("+00:00", "Z")


def test_duplicate_start_preserves_started_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("CLAUDE_SESSION_EVENTS", str(events))

    with events.open("w") as f:
        f.write(
            json.dumps({
                "event": "start",
                "session_id": "S",
                "cwd": "/old",
                "kitty_window_id": 3,
                "timestamp": _ts(20),
            })
            + "\n"
        )
        f.write(
            json.dumps({
                "event": "start",
                "session_id": "S",
                "cwd": "/new",
                "kitty_window_id": 7,
                "timestamp": _ts(2),
            })
            + "\n"
        )

    app = SessionApp()
    app._read_new_events()
    s = app.sessions["S"]
    assert s.cwd == "/new"
    assert s.kitty_window_id == 7
    age_min = (datetime.now(UTC) - s.started_at).total_seconds() / 60
    assert 15 < age_min < 25, f"expected ~20m original start preserved, got {age_min:.1f}m"
