from __future__ import annotations

from klawde.tui import _status_icon


def test_running_is_green_dot() -> None:
    t = _status_icon("running")
    assert t.plain == "●"
    assert "green" in str(t.style)


def test_needs_approval_is_yellow_pause() -> None:
    t = _status_icon("needs_approval")
    assert t.plain == "⏸"
    assert "yellow" in str(t.style)


def test_unknown_defaults_to_running() -> None:
    t = _status_icon("")
    assert t.plain == "●"
    assert "green" in str(t.style)


def test_needs_approval_event_sets_status() -> None:
    from datetime import UTC, datetime

    from klawde.tui import SessionApp

    app = SessionApp()
    sid = "test-session"
    app._apply_event({
        "event": "start",
        "session_id": sid,
        "cwd": "/tmp",
        "timestamp": datetime.now(UTC).isoformat() + "Z",
    })
    assert app.sessions[sid].status == "running"

    app._apply_event({"event": "needs_approval", "session_id": sid})
    assert app.sessions[sid].status == "needs_approval"

    app._apply_event({"event": "working", "session_id": sid})
    assert app.sessions[sid].status == "running"


def test_needs_approval_sorts_first() -> None:
    from datetime import UTC, datetime, timedelta

    from klawde.tui import Session

    now = datetime.now(UTC)
    sessions = {
        "old": Session(
            session_id="old",
            cwd="/tmp",
            kitty_window_id=None,
            started_at=now - timedelta(hours=1),
            status="running",
        ),
        "new": Session(
            session_id="new",
            cwd="/tmp",
            kitty_window_id=None,
            started_at=now,
            status="needs_approval",
        ),
    }

    def sort_key(kv):
        sid, s = kv
        status_order = 0 if s.status == "needs_approval" else 1
        delta = now - s.started_at
        return (status_order, -delta.total_seconds())

    sorted_sids = [sid for sid, _ in sorted(sessions.items(), key=sort_key)]
    assert sorted_sids == ["new", "old"]
