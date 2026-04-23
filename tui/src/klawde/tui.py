from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Static

from klawde.db import STATUS_NEEDS_APPROVAL, RateLimits, Session, SessionRepo
from klawde.transcript import PendingTool, PendingToolCache

EMPTY = "—"


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


def _ctx_bar(pct: int) -> Text:
    if pct < 0:
        pct = 0
    elif pct > 100:
        pct = 100
    if pct < 70:
        color = "green"
    elif pct < 85:
        color = "yellow"
    else:
        color = "red"
    return Text(f"🧠 {pct:>5}%", style=color)


def _fmt_cost(usd: float | None) -> str:
    if usd is None:
        return EMPTY.rjust(7)
    if usd >= 100:
        return f"${usd:,.0f}".rjust(7)
    return f"${usd:.2f}".rjust(7)


def _fmt_resets_at(when: datetime, *, include_date: bool) -> str:
    local = when.astimezone()
    fmt = "%-m/%-d %-I:%M%p" if include_date else "%-I:%M%p"
    return local.strftime(fmt)


def _fmt_pct_cell(pct: float | None) -> str:
    return EMPTY if pct is None else f"{round(pct)}%"


def _fmt_resets_cell(when: datetime | None, *, include_date: bool) -> str:
    return EMPTY if when is None else _fmt_resets_at(when, include_date=include_date)


def _fmt_burn_cell(usd_per_hr: float | None) -> str:
    if usd_per_hr is None or usd_per_hr <= 0:
        return EMPTY
    if usd_per_hr >= 100:
        return f"${usd_per_hr:,.0f}/hr"
    return f"${usd_per_hr:.2f}/hr"


def _summary_row(rl: RateLimits | None, burn_usd_per_hr: float | None) -> tuple[str, ...]:
    r = rl or RateLimits(None, None, None, None)
    return (
        f"⏳ {_fmt_pct_cell(r.five_hour_pct)}",
        f"🔄 {_fmt_resets_cell(r.five_hour_resets_at, include_date=False)}",
        f"📅 {_fmt_pct_cell(r.seven_day_pct)}",
        f"🔄 {_fmt_resets_cell(r.seven_day_resets_at, include_date=True)}",
        f"🔥 {_fmt_burn_cell(burn_usd_per_hr)}",
    )


def _status_icon(status: str) -> Text:
    if status == STATUS_NEEDS_APPROVAL:
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


def _fmt_idle(updated: datetime) -> str:
    # Derived from sessions.updated_at (every hook + statusline tick bumps it).
    # Proxy for "time since last assistant activity."
    delta = datetime.now(UTC) - updated
    secs = int(delta.total_seconds())
    if secs < 0:
        return "0s"
    if secs < 60:
        return f"{secs}s"
    mins, _ = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins}m"


def _fmt_cwd(cwd: str, width: int = 30) -> str:
    if not cwd:
        return EMPTY.ljust(width)
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


def _fmt_branch(branch: str | None, width: int = 30) -> str:
    if not branch:
        return EMPTY.ljust(width)
    if len(branch) <= width:
        return branch.ljust(width)
    return ("…" + branch[-(width - 1) :]).ljust(width)


_MAX_BODY_LINES = 30


def _truncate(value: str, max_lines: int = _MAX_BODY_LINES) -> str:
    lines = value.splitlines()
    if len(lines) <= max_lines:
        return value
    head = "\n".join(lines[:max_lines])
    return f"{head}\n… ({len(lines) - max_lines} more lines)"


def _coerce_str(v: object) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    try:
        return json.dumps(v, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(v)


def _append_field(text: Text, label: str, value: object) -> None:
    s = _coerce_str(value)
    if not s:
        return
    if text.plain:
        text.append("\n")
    text.append(f"{label}:\n", style="bold dim")
    body = _truncate(s)
    for line in body.splitlines() or [""]:
        text.append(f"  {line}\n")


# Per-tool field orderings. Keys listed here are pulled from the input dict
# in order and passed to _append_field; any remaining keys are appended
# afterwards so we don't silently drop data on newer Claude Code releases.
_TOOL_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("command", "description", "run_in_background", "timeout"),
    "Edit": ("file_path", "old_string", "new_string", "replace_all"),
    "Write": ("file_path", "content"),
    "Read": ("file_path", "offset", "limit", "pages"),
    "Grep": ("pattern", "path", "glob", "type", "output_mode", "head_limit",
             "-A", "-B", "-C", "-i", "-n", "multiline"),
    "Glob": ("pattern", "path"),
    "WebFetch": ("url", "prompt"),
    "WebSearch": ("query",),
    "Task": ("subagent_type", "description", "prompt"),
    "TodoWrite": ("todos",),
    "AskUserQuestion": ("questions",),
}


def _fmt_questions(value: object) -> str:
    """Render AskUserQuestion.questions as: question text + numbered options
    with short labels and descriptions. Skips per-option `preview` payloads."""
    if not isinstance(value, list):
        return _coerce_str(value)
    out: list[str] = []
    multi = len(value) > 1
    for i, q in enumerate(value, 1):
        if not isinstance(q, dict):
            continue
        qtext = str(q.get("question") or "").strip()
        if multi:
            out.append(f"Q{i}: {qtext}" if qtext else f"Q{i}:")
        elif qtext:
            out.append(qtext)
        options = q.get("options")
        if isinstance(options, list):
            for j, opt in enumerate(options, 1):
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label") or "").strip()
                desc = str(opt.get("description") or "").strip()
                if label and desc:
                    out.append(f"  [{j}] {label} — {desc}")
                elif label:
                    out.append(f"  [{j}] {label}")
    return "\n".join(out)


def _fmt_todos(value: object) -> str:
    """Render TodoWrite.todos as a bullet list: • [status] subject."""
    if not isinstance(value, list):
        return _coerce_str(value)
    out: list[str] = []
    for t in value:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "").strip()
        subject = str(t.get("subject") or t.get("content") or "").strip()
        if not subject:
            continue
        out.append(f"• [{status}] {subject}" if status else f"• {subject}")
    return "\n".join(out)


# Specialized per-(tool, field) formatters for structured payloads. Return a
# plain string; _append_field handles label + indent + truncation.
_FIELD_FMT: dict[tuple[str, str], Callable[[object], str]] = {
    ("AskUserQuestion", "questions"): _fmt_questions,
    ("TodoWrite", "todos"): _fmt_todos,
}


def _fmt_pending_input(tool_name: str, inp: dict[str, object]) -> Text:
    """Render tool input as Rich Text with per-field labels. Unknown tools
    fall back to dict-iteration order."""
    text = Text()
    ordered = _TOOL_FIELDS.get(tool_name, ())
    seen: set[str] = set()

    def emit(key: str, val: object) -> None:
        fmt = _FIELD_FMT.get((tool_name, key))
        _append_field(text, key, fmt(val) if fmt is not None else val)

    for key in ordered:
        if key in inp:
            emit(key, inp[key])
            seen.add(key)
    for key, val in inp.items():
        if key in seen:
            continue
        emit(key, val)
    return text


class PendingPromptScreen(ModalScreen["Session | None"]):
    """Modal displaying the pending tool call for a session.

    Enter → dismisses with the session (caller focuses its kitty window).
    Space / Escape → dismisses with None.
    """

    DEFAULT_CSS = """
    PendingPromptScreen {
        align: center middle;
    }
    PendingPromptScreen > Vertical {
        width: 80;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    PendingPromptScreen #title  { color: $text-muted; }
    PendingPromptScreen #prompt { margin-top: 1; color: $text; }
    PendingPromptScreen #tool   { color: $warning; text-style: bold; margin-top: 1; }
    PendingPromptScreen #input  { margin-top: 1; }
    PendingPromptScreen #hint   { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [
        Binding("enter", "focus_window", "Focus"),
        Binding("space", "close", "Close"),
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def __init__(self, session: Session, pending: PendingTool) -> None:
        super().__init__()
        self._session = session
        self._pending = pending

    def compose(self) -> ComposeResult:
        sid = self._session.session_id[:8]
        cwd = _fmt_cwd(self._session.cwd, width=60).rstrip()
        body = _fmt_pending_input(self._pending.name, self._pending.input)
        with Vertical():
            yield Static(f"{sid}  {cwd}", id="title")
            if self._pending.user_prompt:
                prompt_text = Text()
                prompt_text.append("prompt:\n", style="bold dim")
                for line in _truncate(self._pending.user_prompt, max_lines=10).splitlines():
                    prompt_text.append(f"  {line}\n")
                yield Static(prompt_text, id="prompt")
            yield Static(self._pending.name, id="tool")
            yield Static(body, id="input")
            yield Static("enter: focus window  ·  space: close", id="hint")

    def action_focus_window(self) -> None:
        self.dismiss(self._session)

    def action_close(self) -> None:
        self.dismiss(None)


class SessionApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    DataTable#sessions { height: 1fr; }
    HeaderTitle { content-align: left middle; padding-left: 1; }
    HeaderIcon { display: none; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "inspect_pending", "Inspect"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("i", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_down", "Down", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._repo = SessionRepo()
        self._pending_cache = PendingToolCache()
        self.sessions: list[Session] = []

    def format_title(self, title: str, sub_title: str) -> Content:
        title_c = Content(title)
        if not sub_title:
            return title_c
        return Content.assemble(title_c, (" · ", "dim"), Content(sub_title).stylize("dim"))

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "klawde"
        sessions = self.query_one("#sessions", DataTable)
        for label in ("", "Ctx", "Location", "Time", "Model", "Kitty"):
            sessions.add_column(Text(label, justify="center"))
        sessions.focus()
        self._tick()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self.sessions = self._repo.list_sessions()
        row = _summary_row(
            self._repo.get_rate_limits(),
            self._repo.get_burn_rate_per_hr(),
        )
        self.sub_title = (
            "" if all(c.endswith(EMPTY) for c in row) else "   ·   ".join(row)
        )
        self._render_table()

    def _render_table(self) -> None:
        table = self.query_one("#sessions", DataTable)
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
            kitty_id = str(s.kitty_window_id) if s.kitty_window_id is not None else EMPTY
            ctx_top: Text | str = (
                _ctx_bar(s.context_percent)
                if s.context_percent is not None
                else " " * 9
            )
            cost_str = _fmt_cost(s.total_cost_usd).strip() or EMPTY
            ctx_cell = Text.assemble(ctx_top, "\n", f"💰 {cost_str.rjust(6)}")
            if s.model:
                model_str = s.model.removeprefix("claude-")
                model_str = re.sub(r"-\d{8}", "", model_str)
            else:
                model_str = EMPTY
            cwd_line = f"📁 {_fmt_cwd(s.cwd)}"
            branch_line = f"🌿 {_fmt_branch(s.git_branch)}" if s.git_branch else " " * 33
            location = Text(f"{cwd_line}\n{branch_line}")
            time_cell = Text(
                f"⏱️ {_fmt_duration(s.started_at).rjust(6)}\n"
                f"💤 {_fmt_idle(s.updated_at).rjust(6)}"
            )
            model_cell = Text(
                f"🤖 {model_str.rjust(12)}\n"
                f"🪪 {s.session_id[:8].rjust(12)}"
            )
            kitty_cell = Text(f"{kitty_id.rjust(4)}\n{' ' * 4}")
            table.add_row(
                _status_icon(s.status),
                ctx_cell,
                location,
                time_cell,
                model_cell,
                kitty_cell,
                height=2,
                key=s.session_id,
            )

        if cursor_row_key:
            try:
                row_index = table.get_row_index(cursor_row_key)
                table.move_cursor(row=row_index)
            except Exception:
                pass

    def action_cursor_up(self) -> None:
        table = self.query_one("#sessions", DataTable)
        if table.cursor_row == 0:
            table.move_cursor(row=table.row_count - 1)
        else:
            table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#sessions", DataTable)
        if table.cursor_row == table.row_count - 1:
            table.move_cursor(row=0)
        else:
            table.action_cursor_down()

    def _selected_session(self) -> Session | None:
        table = self.query_one("#sessions", DataTable)
        if not table.row_count:
            return None
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except Exception:
            return None
        return next((s for s in self.sessions if s.session_id == key), None)

    def _focus_session(self, session: Session) -> None:
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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "sessions":
            return
        key = event.row_key.value if event.row_key else None
        if not key:
            return
        session = next((s for s in self.sessions if s.session_id == key), None)
        if session:
            self._focus_session(session)

    def action_inspect_pending(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        if session.status != STATUS_NEEDS_APPROVAL:
            self.notify("no pending prompt", severity="information", timeout=2)
            return
        if not session.transcript_path:
            self.notify("no transcript path on session", severity="warning", timeout=3)
            return
        pending = self._pending_cache.get(
            session.session_id, Path(session.transcript_path)
        )
        if pending is None:
            changed = self._repo.reset_needs_approval(session.session_id)
            msg = (
                "no pending tool — reset to running"
                if changed
                else "no pending tool — already resolved"
            )
            self.notify(msg, severity="warning", timeout=3)
            self._tick()
            return
        self.push_screen(PendingPromptScreen(session, pending), self._on_modal_dismiss)

    def _on_modal_dismiss(self, session: Session | None) -> None:
        if session is not None:
            self._focus_session(session)

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


def _prune_async() -> None:
    """Fire-and-forget retention + zombie reap at startup.

    prune.sh is idempotent and fast (~tens of ms); running it here means the
    first list_sessions() never sees rows left stale by a machine suspend."""
    import subprocess
    script = Path.home() / ".klawde" / "prune.sh"
    if not script.exists():
        return
    try:
        subprocess.Popen(
            ["bash", str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def main() -> None:
    _prune_async()
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
