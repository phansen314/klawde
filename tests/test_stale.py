from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from klawde.tui import SessionApp


def _ts(mins_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=mins_ago)).isoformat().replace("+00:00", "Z")


def _make_transcript(home: Path, cwd: str, session_id: str, mtime_mins_ago: int) -> None:
    munged = cwd.rstrip("/").replace("/", "-") or "-"
    d = home / ".claude" / "projects" / munged
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session_id}.jsonl"
    f.write_text("{}\n")
    t = time.time() - mtime_mins_ago * 60
    os.utime(f, (t, t))


def test_prune_drops_stale_keeps_fresh_and_young(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_SESSION_EVENTS", str(events))

    _make_transcript(tmp_path, "/x/A", "A-fresh", mtime_mins_ago=2)
    _make_transcript(tmp_path, "/x/B", "B-stale", mtime_mins_ago=60)

    with events.open("w") as f:
        for sid, cwd, mins in [
            ("A-fresh", "/x/A", 60),
            ("B-stale", "/x/B", 60),
            ("C-notx", "/x/C", 60),
            ("D-young", "/x/D", 5),
        ]:
            f.write(
                json.dumps({
                    "event": "start",
                    "session_id": sid,
                    "cwd": cwd,
                    "kitty_window_id": None,
                    "timestamp": _ts(mins),
                })
                + "\n"
            )

    app = SessionApp()
    app._read_new_events()
    assert set(app.sessions) == {"A-fresh", "B-stale", "C-notx", "D-young"}

    app._prune_stale()
    assert set(app.sessions) == {"A-fresh", "D-young"}
