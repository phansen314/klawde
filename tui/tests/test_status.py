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
