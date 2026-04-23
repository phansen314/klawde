# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What klawde is

A Textual TUI that lists currently-open Claude Code sessions and, on Enter, focuses the kitty window where the selected session is running. It is a "jump back to where I was" tool for users juggling several Claude Code sessions across kitty windows/tabs.

## Architecture (data flow)

```
Claude Code hooks
  • SessionStart, SessionEnd, Notification, PostToolUse, statusline
        │
        ▼
metrics/*.sh (bash + jq + sqlite3)
  • session_start.sh   — INSERT sessions row
  • session_end.sh     — UPDATE status='stopped', stopped_at
  • notification.sh    — UPDATE status='needs_approval' on permission prompts
  • post_tool_use.sh   — UPDATE status='running' after tool resume
  • kitty_start.sh     — UPSERT kitty window_id/listen_on into session_metadata
  • statusline.sh      — UPDATE live metrics (model, context%, cost, rate limits,
                          tokens, lines added/removed, …) on every tick
        │
        ▼
~/.klawde/sessions.db   (SQLite, WAL mode)
  • sessions           — per-session row, ~25 columns
  • session_metadata   — namespaced key-value (kitty namespace: window_id, listen_on)
  • events             — append-only audit log
        │
        ▼
tui/src/klawde/db.py (SessionRepo)
  • opens read-only connection (file:...?mode=ro)
  • one SQL query joining sessions + session_metadata for the kitty namespace
  • returns list[Session] ordered: needs_approval first, then started_at DESC
        │
        ▼
tui/src/klawde/tui.py (SessionApp)
  • polls SessionRepo.list_sessions() every 1s
  • renders a DataTable; Enter focuses the selected row's kitty window via
    `kitten @ --to $KITTY_LISTEN_ON focus-window --match id:<kitty_window_id>`
```

The TUI is single-process; no daemon. It is launched (typically from a dedicated kitty window) via `uv run klawde` or the installed `klawde` script.

### klawde's own window registration

`main()` in `tui/src/klawde/tui.py` writes `${XDG_RUNTIME_DIR:-/tmp}/klawde.window` on startup with two lines — klawde's own `KITTY_WINDOW_ID` and `KITTY_LISTEN_ON` — atomically via tempfile+rename, and unlinks it on exit. Any stale file from a prior crashed run is cleared on entry.

This file is consumed by `~/.config/kitty/focus-klawde.sh` (not in this repo), bound to `ctrl+space>c` in the user's kitty config, so the user can jump back to the klawde window itself from anywhere.

### Hook wiring

All hooks in `metrics/` must be wired into `~/.claude/settings.json` — `bash metrics/setup.sh` does not do this automatically. Two hooks must run **blocking** (no `async: true`):
- `session_start.sh` — creates the `sessions` row before `kitty_start.sh` appends to `session_metadata` (FK-safe by construction).
- `session_end.sh` — writes `status='stopped'` + `stopped_at` before the Claude Code process exits. Async on a fast `/exit` loses the race and sessions stay `running` until pruned.

The remaining hooks (`kitty_start.sh`, `notification.sh`, `post_tool_use.sh`) can run async. `statusline.sh` is wired via `statusLine.command`. Hooks are bash+jq+sqlite3 — Python cold-start would block SessionStart.

## Commands (uv)

```bash
cd tui
uv sync --extra dev                    # install runtime + dev deps
uv run klawde                          # start the TUI against ~/.klawde/sessions.db
uv run scripts/seed_test_data.py       # seed /tmp/klawde-test/sessions.db
KLAWDE_DB=/tmp/klawde-test/sessions.db uv run klawde   # TUI against seed DB
uv run ruff check .                    # lint
uv run ruff format .                   # format
uv run mypy src                        # type-check
uv run python -m pytest                # tests (see note below on why `-m pytest`)
uv run python -m pytest tests/test_db.py::test_needs_approval_sorts_first   # single test
```

## Table columns

Column order: `● | Ctx | CWD | Branch | Duration | Idle | Model | Cost | Session | Kitty`

- **●** — status icon: green ● running, yellow ⏸ needs_approval. From `sessions.status`.
- **Ctx** — context window usage bar + %. Color: green <70%, yellow 70–84%, red ≥85% with ⚠. From `sessions.context_percent`, rendered by `_ctx_bar`.
- **CWD** — working directory, home-collapsed (`~/...`), left-ellipsis truncated to 30 chars. From `sessions.cwd`, rendered by `_fmt_cwd`.
- **Branch** — git branch (or 7-char short sha when detached), left-ellipsis truncated to 20 chars. From `sessions.git_branch`, rendered by `_fmt_branch`. Populated on every statusline tick by a zero-fork pure-bash walk from `cwd` to the nearest `.git/HEAD` (regular repo or linked worktree).
- **Duration** — time since session started. From `sessions.started_at`.
- **Idle** — time since last update. From `sessions.updated_at`.
- **Model** — model name with `claude-` prefix and date suffix stripped. From `sessions.model`.
- **Cost** — total_cost_usd with burn-rate context via the summary row.
- **Session** — first 8 chars of session UUID.
- **Kitty** — kitty window ID. From `session_metadata` (namespace=kitty, key=window_id).

## Key files

- `tui/src/klawde/tui.py` — entire TUI, rendering, focus action, self-registration in `main()`.
- `tui/src/klawde/db.py` — `SessionRepo` + `Session` dataclass. Single SQL query, read-only connection.
- `tui/src/klawde/__main__.py` — thin entry re-exporting `main`.
- `tui/scripts/seed_test_data.py` — seeds a disposable SQLite DB at `/tmp/klawde-test/sessions.db`.
- `tui/pyproject.toml` — hatchling + uv, ruff (line 100, py312), mypy, pytest-asyncio/xdist/cov.

## metrics/ — SQLite data layer

The single source of truth. All hooks write to `~/.klawde/sessions.db`; the TUI reads from it read-only.

**Install (one-time, idempotent):**
```bash
bash metrics/setup.sh
```

**Schema:** three tables — `sessions` (per-session state, ~25 columns including cost, tokens, rate limits, git worktree, Claude Code version), `session_metadata` (namespaced KV; kitty namespace written by `kitty_start.sh`), `events` (append-only audit log). WAL mode, `auto_vacuum=INCREMENTAL`, `synchronous=NORMAL`.

**Key files:**
- `metrics/setup.sh` — copies scripts to `~/.klawde/`, creates DB + schema, idempotent `ALTER TABLE ADD COLUMN` loop for in-place schema evolution
- `metrics/common.sh` — shared helpers: `sq_to`, `num_or_null_to`, `sq_or_null_to`, `stline_cache_path_to`, `now()`, `db()`
- `metrics/session_start.sh` / `session_end.sh` / `notification.sh` / `post_tool_use.sh` — lifecycle hooks
- `metrics/kitty_start.sh` — captures `KITTY_WINDOW_ID` / `KITTY_LISTEN_ON` into `session_metadata` on SessionStart. Runs after `session_start.sh` (which is blocking) to avoid FK races.
- `metrics/statusline.sh` — per-tick UPDATE of live metrics + emoji-rich two-line output
- `metrics/prune.sh` — retention: events >30d, stopped sessions >90d
- `metrics/test_data.sh` — seed 4 fake sessions for manual hook testing
- `metrics/simulate_hook.sh` — pipe JSON to a named hook and inspect DB state

**Statusline:** `~/.klawde/statusline.sh` is a data-writing primitive that also prints the user-facing summary. Downstream composition (e.g., caveman) is the operator's responsibility; `setup.sh` intentionally does not wire it and instead prints an action-required banner pointing at `README.md` for examples.

## Conventions worth knowing

- Python 3.12+, `from __future__ import annotations` in source files.
- Ruff selects `E, F, I, UP, W`, ignores `E501` (line length is a soft 100).
- Mypy ignores missing imports; `no-any-return` disabled.
- DB reads are read-only: `SessionRepo` opens with `file:...?mode=ro` URI. Transient `sqlite3.Error` (DB missing, mid-WAL-checkpoint) is swallowed and returns `[]`; next tick retries. WAL mode means readers never block writers.
- All `metrics/*.sh` scripts are **bash 3.2 compatible** so they run on macOS `/bin/bash` (Apple froze there for licensing) as well as Linux bash 4/5. No `${var^}`, no `mapfile`, no associative arrays.
- `INPUT=$(cat)` for stdin capture in hooks — do NOT use `$(</dev/stdin)`; Claude Code's invocation makes that read empty.
- kitty remote control is assumed: `allow_remote_control yes` + `listen_on unix:/tmp/kitty-{kitty_pid}` in the user's kitty.conf. When this is missing, klawde surfaces the error via `self.notify(..., severity="error")` instead of crashing.
- Session row `ON CONFLICT DO UPDATE` overwrites `cwd` and kitty metadata on resume while preserving `started_at`. Continuation semantics: same session, fresh location, Duration reflects total age.
- Retention (`metrics/prune.sh`) handles stale cleanup; the TUI itself no longer prunes.
- Run tests with `uv run python -m pytest`, not `uv run pytest`. The latter resolves via PATH to the pyenv shim, bypassing the project venv and failing with `ModuleNotFoundError: klawde`.
