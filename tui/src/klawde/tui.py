from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

from klawde.db import Session, SessionRepo


def _context_window(model: str | None) -> int:
    if not model:
        return 200_000
    if "[1m]" in model.lower():
        return 1_000_000
    if "opus-4-7" in model:
        return 1_000_000
    return 200_000


def _context_percent(tokens: int, window: int) -> int:
    if window <= 0:
        return 0
    pct = round(tokens * 100 / window)
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return pct


_BAR_WIDTH = 10


def _ctx_bar(pct: int) -> Text:
    filled = round(pct * _BAR_WIDTH / 100)
    if filled < 0:
        filled = 0
    elif filled > _BAR_WIDTH:
        filled = _BAR_WIDTH
    bar = "█" * filled + " " * (_BAR_WIDTH - filled)
    if pct < 70:
        color = "green"
    elif pct < 85:
        color = "yellow"
    else:
        color = "red"
    suffix = " ⚠" if pct >= 85 else ""
    return Text(f"{bar} {pct}%{suffix}", style=color)


def _status_icon(status: str) -> Text:
    if status == "needs_approval":
        return Text("⏸", style="yellow")
    return Text("●", style="green")


def _fmt_duration(started: datetime) -> str:
    delta = datetime.now(UTC) - started
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    mins, _ = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins}m"


def _fmt_cwd(cwd: str, width: int = 30) -> str:
    if not cwd:
        return "—".ljust(width)
    p = Path(cwd)
    try:
        collapsed = "~/" + str(p.relative_to(Path.home()))
    except ValueError:
        collapsed = str(p)
    if len(collapsed) <= width:
        return collapsed.ljust(width)
    parts = p.parts
    for n in range(len(parts) - 1, 0, -1):
        candidate = "…/" + "/".join(parts[-n:])
        if len(candidate) <= width:
            return candidate.ljust(width)
    return ("…" + p.name)[:width].ljust(width)


class SessionApp(App):
    CSS = """
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("i", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_down", "Down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._repo = SessionRepo()
        self.sessions: list[Session] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "klawde"
        self.sub_title = "Enter: focus Kitty pane  ·  q: quit"
        table = self.query_one(DataTable)
        table.add_columns("", "Ctx", "CWD", "Duration", "Model", "Session", "Kitty")
        self._tick()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self.sessions = self._repo.list_sessions()
        self._render_table()

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        cursor_row_key = None
        if table.row_count:
            try:
                cursor_row_key = table.coordinate_to_cell_key(
                    table.cursor_coordinate
                ).row_key.value
            except Exception:
                cursor_row_key = None

        table.clear()

        for s in self.sessions:
            kitty = (
                str(s.kitty_window_id) if s.kitty_window_id is not None else "—"
            ).rjust(4)
            ctx: Text | str = (
                _ctx_bar(s.context_percent)
                if s.context_percent is not None
                else "—".ljust(15)
            )
            if s.model:
                model_str = s.model.removeprefix("claude-")
                model_str = re.sub(r"-\d{8}", "", model_str)
                model = model_str.ljust(15)
            else:
                model = "—".ljust(15)
            cwd = _fmt_cwd(s.cwd)
            table.add_row(
                _status_icon(s.status),
                ctx,
                cwd,
                _fmt_duration(s.started_at).rjust(5),
                model,
                s.session_id[:8],
                kitty,
                key=s.session_id,
            )

        if cursor_row_key:
            try:
                row_index = table.get_row_index(cursor_row_key)
                table.move_cursor(row=row_index)
            except Exception:
                pass

    def action_cursor_up(self) -> None:
        table = self.query_one(DataTable)
        if table.cursor_row == 0:
            table.move_cursor(row=table.row_count - 1)
        else:
            table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one(DataTable)
        if table.cursor_row == table.row_count - 1:
            table.move_cursor(row=0)
        else:
            table.action_cursor_down()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value if event.row_key else None
        if not key:
            return
        session = next((s for s in self.sessions if s.session_id == key), None)
        if not session:
            return
        if session.kitty_window_id is None:
            self.notify("no kitty window id for this session", severity="warning")
            return
        listen_on = os.environ.get("KITTY_LISTEN_ON", "").strip()
        if not listen_on:
            self.notify(
                "KITTY_LISTEN_ON unset — restart kitty to enable remote control",
                severity="error",
            )
            return
        self._focus_kitty_window(listen_on, session.kitty_window_id)

    @work(exclusive=True, group="focus-kitty")
    async def _focus_kitty_window(self, listen_on: str, window_id: int) -> None:
        cmd = [
            "kitten", "@", "--to", listen_on,
            "focus-window", "--match", f"id:{window_id}",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self.notify("kitten not found on PATH", severity="error")
            return
        try:
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            except TimeoutError:
                self.notify("kitten timed out", severity="error")
                return
            if proc.returncode != 0:
                msg = (
                    stderr.decode(errors="replace")
                    or stdout.decode(errors="replace")
                    or "kitten failed"
                ).strip()
                self.notify(msg[:200], severity="error")
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()


def _state_file() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "klawde.window"


def main() -> None:
    sf = _state_file()
    try:
        sf.unlink()
    except (FileNotFoundError, OSError):
        pass
    wid = os.environ.get("KITTY_WINDOW_ID")
    listen = os.environ.get("KITTY_LISTEN_ON", "")
    if wid:
        tmp = sf.with_suffix(sf.suffix + ".tmp")
        try:
            tmp.write_text(f"{wid}\n{listen}\n")
            tmp.replace(sf)
        except OSError:
            pass
    try:
        SessionApp().run()
    finally:
        try:
            sf.unlink()
        except (FileNotFoundError, OSError):
            pass


if __name__ == "__main__":
    main()
