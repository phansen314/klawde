from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header


@dataclass
class Session:
    session_id: str
    cwd: str
    kitty_window_id: int | None
    started_at: datetime


def _events_path() -> Path:
    env = os.environ.get("CLAUDE_SESSION_EVENTS")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "session-events.jsonl"


def _parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


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


class SessionApp(App):
    CSS = """
    DataTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.events_path = _events_path()
        self.sessions: dict[str, Session] = {}
        self._file_offset = 0
        self._file_inode: int | None = None
        self._file_size = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "klawde"
        self.sub_title = "Enter: focus Kitty pane  ·  q: quit"
        table = self.query_one(DataTable)
        table.add_columns("Session", "CWD", "Duration", "Kitty")
        self._tick()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self._read_new_events()
        self._render_table()

    def _read_new_events(self) -> None:
        try:
            st = self.events_path.stat()
        except FileNotFoundError:
            if self.sessions:
                self.sessions.clear()
            self._file_inode = None
            self._file_offset = 0
            self._file_size = 0
            return

        reset = False
        if self._file_inode is None:
            reset = True
        elif st.st_ino != self._file_inode:
            reset = True
        elif st.st_size < self._file_size:
            reset = True

        if reset:
            self.sessions.clear()
            self._file_offset = 0
            self._file_inode = st.st_ino

        self._file_size = st.st_size

        if st.st_size <= self._file_offset:
            return

        try:
            with self.events_path.open("rb") as f:
                f.seek(self._file_offset)
                chunk = f.read()
        except OSError:
            return

        last_nl = chunk.rfind(b"\n")
        if last_nl == -1:
            return
        complete = chunk[: last_nl + 1]
        self._file_offset += last_nl + 1

        for raw in complete.splitlines():
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._apply_event(obj)

    def _apply_event(self, obj: dict) -> None:
        event = obj.get("event")
        sid = obj.get("session_id")
        if not sid or not event:
            return
        if event == "start":
            try:
                started = _parse_ts(obj["timestamp"])
            except (KeyError, ValueError):
                started = datetime.now(UTC)
            self.sessions[sid] = Session(
                session_id=sid,
                cwd=obj.get("cwd", ""),
                kitty_window_id=obj.get("kitty_window_id"),
                started_at=started,
            )
        elif event == "stop":
            self.sessions.pop(sid, None)

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        cursor_row_key = None
        if table.row_count:
            try:
                cursor_row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            except Exception:
                cursor_row_key = None

        table.clear()
        for sid, s in sorted(self.sessions.items(), key=lambda kv: kv[1].started_at):
            kitty = str(s.kitty_window_id) if s.kitty_window_id is not None else "—"
            table.add_row(
                sid[:8],
                Path(s.cwd).name or s.cwd or "—",
                _fmt_duration(s.started_at),
                kitty,
                key=sid,
            )

        if cursor_row_key and cursor_row_key in self.sessions:
            try:
                row_index = table.get_row_index(cursor_row_key)
                table.move_cursor(row=row_index)
            except Exception:
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value if event.row_key else None
        if not key:
            return
        session = self.sessions.get(key)
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
        cmd = [
            "kitten", "@", "--to", listen_on,
            "focus-window", "--match", f"id:{session.kitty_window_id}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except FileNotFoundError:
            self.notify("kitten not found on PATH", severity="error")
            return
        except subprocess.TimeoutExpired:
            self.notify("kitten timed out", severity="error")
            return
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "kitten failed").strip()
            self.notify(msg[:200], severity="error")


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
