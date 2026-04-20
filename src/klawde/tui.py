from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header

_TAIL_BYTES = 16 * 1024
_STALE_SECONDS = 30 * 60

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


@dataclass
class Session:
    session_id: str
    cwd: str
    kitty_window_id: int | None
    started_at: datetime
    source: str | None = None
    model: str | None = None
    context_tokens: int | None = None
    context_percent: int | None = None


def _events_path() -> Path:
    env = os.environ.get("CLAUDE_SESSION_EVENTS")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "session-events.jsonl"


def _transcript_path(cwd: str, session_id: str) -> Path | None:
    if not cwd or not session_id:
        return None
    normalized = cwd.rstrip("/") or "/"
    munged = normalized.replace("/", "-")
    if not munged:
        return None
    p = Path.home() / ".claude" / "projects" / munged / f"{session_id}.jsonl"
    return p if p.exists() else None


@dataclass
class TranscriptMeta:
    model: str | None
    context_tokens: int


def _read_transcript_meta(path: Path) -> TranscriptMeta | None:
    """Tail the transcript and return the main-chain entry with the most recent
    timestamp. Returns model + context_tokens (input + cache_read + cache_creation).

    Filters out: entries without `message.usage`, sidechain entries
    (`isSidechain == true`), API-error entries (`isApiErrorMessage == true`),
    and entries missing a `timestamp`. Schema is verified implicitly —
    non-Claude-Code files produce None.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - _TAIL_BYTES))
            chunk = f.read()
    except OSError:
        return None
    if size > _TAIL_BYTES:
        nl = chunk.find(b"\n")
        if nl == -1:
            return None
        chunk = chunk[nl + 1:]

    best_ts: str | None = None
    best_model: str | None = None
    best_ctx: int | None = None
    for raw in chunk.splitlines():
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("isSidechain"):
            continue
        if obj.get("isApiErrorMessage"):
            continue
        ts = obj.get("timestamp")
        if not ts:
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        try:
            ctx = (
                int(usage.get("input_tokens", 0))
                + int(usage.get("cache_read_input_tokens", 0))
                + int(usage.get("cache_creation_input_tokens", 0))
            )
        except (TypeError, ValueError):
            continue
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_model = msg.get("model") if isinstance(msg.get("model"), str) else None
            best_ctx = ctx

    if best_ctx is None:
        return None
    return TranscriptMeta(model=best_model, context_tokens=best_ctx)


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
        table.add_columns("Session", "CWD", "Duration", "Kitty", "Ctx")
        self._tick()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self._read_new_events()
        self._prune_stale()
        self._refresh_metas()
        self._render_table()

    def _refresh_metas(self) -> None:
        for sid, s in self.sessions.items():
            tp = _transcript_path(s.cwd, sid)
            if tp is None:
                s.model = None
                s.context_tokens = None
                s.context_percent = None
                continue
            meta = _read_transcript_meta(tp)
            if meta is None:
                continue
            s.model = meta.model
            s.context_tokens = meta.context_tokens
            s.context_percent = _context_percent(meta.context_tokens, _context_window(meta.model))

    def _prune_stale(self) -> None:
        now = time.time()
        stale: list[str] = []
        for sid, s in self.sessions.items():
            age = now - s.started_at.timestamp()
            if age < _STALE_SECONDS:
                continue
            tp = _transcript_path(s.cwd, sid)
            if tp is not None:
                try:
                    mtime = tp.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if now - mtime < _STALE_SECONDS:
                    continue
            stale.append(sid)
        for sid in stale:
            self.sessions.pop(sid, None)

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
            self._file_inode = st.st_ino
            self._file_offset = max(0, st.st_size - _TAIL_BYTES)

        self._file_size = st.st_size

        if st.st_size <= self._file_offset:
            return

        try:
            with self.events_path.open("rb") as f:
                f.seek(self._file_offset)
                chunk = f.read()
        except OSError:
            return

        if reset and self._file_offset > 0:
            first_nl = chunk.find(b"\n")
            if first_nl == -1:
                return
            chunk = chunk[first_nl + 1:]
            self._file_offset += first_nl + 1

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
            existing = self.sessions.get(sid)
            self.sessions[sid] = Session(
                session_id=sid,
                cwd=obj.get("cwd", ""),
                kitty_window_id=obj.get("kitty_window_id"),
                started_at=existing.started_at if existing else started,
                source=obj.get("source"),
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
            ctx = f"{s.context_percent}%" if s.context_percent is not None else "—"
            table.add_row(
                sid[:8],
                Path(s.cwd).name or s.cwd or "—",
                _fmt_duration(s.started_at),
                kitty,
                ctx,
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
                msg = (stderr.decode(errors="replace") or stdout.decode(errors="replace") or "kitten failed").strip()
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
