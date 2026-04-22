from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.app import App, ComposeResult

from klawde.db import Session
from klawde.transcript import PendingTool
from klawde.tui import PendingPromptScreen


def _session(sid: str = "aaaaaaaa1111") -> Session:
    now = datetime.now(UTC)
    return Session(
        session_id=sid,
        cwd="/tmp/demo",
        status="needs_approval",
        model="claude-opus-4-7",
        context_percent=12,
        started_at=now,
        kitty_window_id=3,
        total_cost_usd=0.12,
        updated_at=now,
        transcript_path="/tmp/demo.jsonl",
    )


def _pending() -> PendingTool:
    return PendingTool(
        name="Bash",
        tool_use_id="toolu_test",
        input={"command": "rm -rf node_modules", "description": "clean"},
        user_prompt="please remove node_modules",
    )


class _Host(App[None]):
    """Minimal host app so ModalScreen has an App context."""

    def __init__(self, session: Session, pending: PendingTool) -> None:
        super().__init__()
        self._session = session
        self._pending = pending
        self.dismissed_with: object = "unset"

    def compose(self) -> ComposeResult:
        return iter(())

    async def on_mount(self) -> None:
        self.push_screen(
            PendingPromptScreen(self._session, self._pending),
            lambda result: setattr(self, "dismissed_with", result),
        )


@pytest.mark.asyncio
async def test_modal_enter_dismisses_with_session() -> None:
    session = _session()
    app = _Host(session, _pending())
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
    assert app.dismissed_with is session


@pytest.mark.asyncio
async def test_modal_space_dismisses_with_none() -> None:
    app = _Host(_session(), _pending())
    async with app.run_test() as pilot:
        await pilot.press("space")
        await pilot.pause()
    assert app.dismissed_with is None


@pytest.mark.asyncio
async def test_modal_escape_dismisses_with_none() -> None:
    app = _Host(_session(), _pending())
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
    assert app.dismissed_with is None


@pytest.mark.asyncio
async def test_modal_renders_prompt_tool_and_input() -> None:
    app = _Host(_session(), _pending())
    async with app.run_test() as pilot:
        screen = app.screen
        from textual.widgets import Static

        def _text(w: Static) -> str:
            r = w.render()
            return r.plain if hasattr(r, "plain") else str(r)

        joined = "\n".join(_text(w) for w in screen.query(Static))
        assert "aaaaaaaa" in joined           # session id prefix
        assert "please remove node_modules" in joined  # user_prompt
        assert "Bash" in joined               # tool name
        assert "rm -rf node_modules" in joined  # command input
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_modal_without_user_prompt_skips_prompt_block() -> None:
    pending = PendingTool(
        name="Bash", tool_use_id="t", input={"command": "ls"}, user_prompt=None,
    )
    app = _Host(_session(), pending)
    async with app.run_test() as pilot:
        from textual.widgets import Static

        screen = app.screen
        ids = {w.id for w in screen.query(Static)}
        assert "prompt" not in ids
        assert "tool" in ids
        assert "input" in ids
        await pilot.press("escape")
        await pilot.pause()
