# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What klawde is

A Textual TUI that lists currently-open Claude Code sessions and, on Enter, focuses the kitty window where the selected session is running. It is a "jump back to where I was" tool for users juggling several Claude Code sessions across kitty windows/tabs.

## Architecture (event flow)

```
Claude Code SessionStart/Stop hook
        │
        ▼
hooks/emit-session-event.sh
  • reads stdin JSON from Claude Code
  • appends one line to $CLAUDE_SESSION_EVENTS
    (default: ~/.claude/session-events.jsonl)
  • captures KITTY_WINDOW_ID at session start
        │
        ▼
~/.claude/session-events.jsonl  (append-only jsonl)
        │
        ▼
klawde TUI (src/klawde/tui.py — SessionApp)
  • tails the jsonl by inode + byte offset (handles truncation/rotation)
  • maintains an in-memory dict of live sessions
  • on Enter: subprocess kitten @ --to $KITTY_LISTEN_ON
                focus-window --match id:<kitty_window_id>
```

The TUI is single-process; no daemon. It is launched (typically from a dedicated kitty window) via `uv run klawde` or the installed `klawde` script.

### klawde's own window registration

`main()` in `src/klawde/tui.py` writes `${XDG_RUNTIME_DIR:-/tmp}/klawde.window` on startup with two lines — klawde's own `KITTY_WINDOW_ID` and `KITTY_LISTEN_ON` — atomically via tempfile+rename, and unlinks it on exit. Any stale file from a prior crashed run is cleared on entry.

This file is consumed by `~/.config/kitty/focus-klawde.sh` (not in this repo), bound to `ctrl+space>c` in the user's kitty config, so the user can jump back to the klawde window itself from anywhere.

### Hook wiring (outside repo)

`hooks/emit-session-event.sh` must be referenced from Claude Code's own settings (e.g. `~/.claude/settings.json`) under SessionStart and SessionEnd hooks. The hook is intentionally bash+jq, not Python, because Python cold-start blocks SessionStart.

## Commands (uv)

```bash
uv sync --extra dev             # install runtime + dev deps
uv run klawde                   # start the TUI
uv run scripts/seed_test_data.py   # seed /tmp/klawde-test/session-events.jsonl
CLAUDE_SESSION_EVENTS=/tmp/klawde-test/session-events.jsonl uv run klawde
uv run ruff check .             # lint
uv run ruff format .            # format
uv run mypy src                 # type-check
uv run python -m pytest         # tests (see note below on why `-m pytest`)
uv run python -m pytest tests/test_dup.py::test_duplicate_start_preserves_started_at   # single test
```

## Key files

- `src/klawde/tui.py` — entire TUI, event-tailing logic, focus action, self-registration in `main()`.
- `src/klawde/__main__.py` — thin entry re-exporting `main`.
- `hooks/emit-session-event.sh` — the Claude Code hook.
- `scripts/seed_test_data.py` — writes synthetic events to `/tmp/klawde-test/`.
- `pyproject.toml` — hatchling + uv, ruff (line 100, py312), mypy, pytest-asyncio/xdist/cov.

## Conventions worth knowing

- Python 3.12+, `from __future__ import annotations` in source files.
- Ruff selects `E, F, I, UP, W`, ignores `E501` (line length is a soft 100).
- Mypy ignores missing imports; `no-any-return` disabled.
- Event-log IO is best-effort — every filesystem interaction is guarded against races (inode change, truncation, partial-write boundaries). Preserve this behavior when touching `_read_new_events`.
- kitty remote control is assumed: `allow_remote_control yes` + `listen_on unix:/tmp/kitty-{kitty_pid}` in the user's kitty.conf. When this is missing, klawde surfaces the error via `self.notify(..., severity="error")` instead of crashing.
- Duplicate `start` events for the same `session_id` (resume, re-hook) overwrite `cwd` and `kitty_window_id` but preserve the original `started_at`. Continuation semantics: same session, fresh location, Duration reflects total age.
- Sessions are pruned if >30 min old AND (transcript mtime >30 min stale OR transcript missing). Young sessions are never pruned, avoiding startup races. See `_prune_stale`.
- Run tests with `uv run python -m pytest`, not `uv run pytest`. The latter resolves via PATH to the pyenv shim, bypassing the project venv and failing with `ModuleNotFoundError: klawde`.
