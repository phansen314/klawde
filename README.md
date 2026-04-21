# klawde

A [Textual](https://textual.textualize.io/) TUI for managing multiple Claude Code sessions in [kitty](https://sw.kovidgoyal.net/kitty/). Shows all live sessions with status, context usage, CWD, model, and duration. Press Enter to jump to that session's kitty window.

```
● | Ctx        | CWD                | Duration | Model   | Session | Kitty
──┼────────────┼────────────────────┼──────────┼─────────┼─────────┼──────
⏸ | ████░ 62%  | ~/code/myapp       | 00:14:22 | sonnet  | a3f9b2c1| 7
● | ██░░░ 31%  | ~/code/klawde      | 00:03:07 | opus    | 9d1e4f82| 4
```

## Prerequisites

- [kitty terminal](https://sw.kovidgoyal.net/kitty/) with remote control enabled
- [Claude Code](https://claude.ai/code) CLI (`claude`)
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or `brew install uv`)
- `jq` and `sqlite3` (`apt install jq sqlite3` / `brew install jq sqlite3`)

## Installation

### 1. Clone and install

```bash
git clone https://github.com/phansen314/klawde.git
cd klawde
uv sync --directory tui
```

### 2. Install the SQLite data layer

```bash
bash metrics/setup.sh
```

This copies the hook scripts to `~/.klawde/`, creates `~/.klawde/sessions.db` with WAL mode, and prints an action-required banner. `setup.sh` intentionally does **not** modify `~/.claude/settings.json` — you wire the hooks and statusline yourself in steps 4 and 5.

### 3. Configure kitty

Add to `~/.config/kitty/kitty.conf`:

```conf
allow_remote_control yes
listen_on unix:/tmp/kitty-{kitty_pid}
```

Restart kitty (or reload config with `ctrl+shift+f5`) for changes to take effect.

> **Why:** klawde uses `kitten @` to focus windows by ID. `allow_remote_control` enables the IPC
> socket; `listen_on` sets the socket path. The `{kitty_pid}` placeholder makes each kitty
> instance use a unique socket so multiple kitty instances don't collide.

### 4. Wire Claude Code hooks

Add the following to `~/.claude/settings.json` (merge with existing `hooks` if present):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "/home/YOU/.klawde/session_start.sh" },
          { "type": "command", "command": "/home/YOU/.klawde/kitty_start.sh", "async": true }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "/home/YOU/.klawde/session_end.sh", "async": true }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          { "type": "command", "command": "/home/YOU/.klawde/notification.sh", "async": true }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "/home/YOU/.klawde/post_tool_use.sh", "async": true }
        ]
      }
    ]
  }
}
```

Replace `/home/YOU` with your home path. `session_start.sh` is **blocking** (no `async`) so it wins the race — `kitty_start.sh` then appends to `session_metadata` with the `sessions` FK row already in place.

### 5. Wire the statusline

`~/.klawde/statusline.sh` writes live metrics (model, context %, cost, rate limits, …) to the DB every tick and prints a two-line emoji summary. In `~/.claude/settings.json`:

*A) klawde only:*

```json
"statusLine": { "type": "command", "command": "/home/YOU/.klawde/statusline.sh" }
```

*B) klawde + another statusline (e.g. caveman)* via a small wrapper script — see the klawde README section *Composing statuslines* below.

### 6. (Optional) Jump back to klawde from any window

klawde writes its own kitty window ID to `${XDG_RUNTIME_DIR:-/tmp}/klawde.window` on startup, so you can bind a hotkey to focus it from anywhere. The script is included at `tui/kitty/focus-klawde.sh`.

```bash
chmod +x /path/to/klawde/tui/kitty/focus-klawde.sh
```

Bind it in `kitty.conf`:

```conf
# Single key
map ctrl+shift+0 launch --type=background /path/to/klawde/tui/kitty/focus-klawde.sh
# Or a chord
map ctrl+space>c launch --type=background /path/to/klawde/tui/kitty/focus-klawde.sh
```

> **Note:** `launch --type=background` drops `KITTY_LISTEN_ON`, so `focus-klawde.sh` reads the
> socket path from `klawde.window` rather than the environment.

## Running

```bash
# From the repo
cd tui
uv run klawde

# Or if installed as a tool
klawde
```

Point klawde at a different DB (useful for testing):

```bash
KLAWDE_DB=/tmp/klawde-test/sessions.db uv run klawde
```

Seed synthetic test data:

```bash
uv run scripts/seed_test_data.py
KLAWDE_DB=/tmp/klawde-test/sessions.db uv run klawde
```

## Keybindings

| Key        | Action                              |
|------------|-------------------------------------|
| `Enter`    | Focus the selected session's window |
| `q` / `Q`  | Quit                                |
| `↑` / `i`  | Move cursor up                      |
| `↓` / `k`  | Move cursor down                    |

## Environment variables

| Variable          | Default                     | Purpose                         |
|-------------------|-----------------------------|---------------------------------|
| `KLAWDE_DB`       | `~/.klawde/sessions.db`     | Path to the SQLite DB           |
| `KITTY_LISTEN_ON` | *(set by kitty)*            | Socket for remote control       |
| `KITTY_WINDOW_ID` | *(set by kitty)*            | Window ID captured at hook time |

## metrics — SQLite data layer

The single source of truth. `metrics/*.sh` hooks write to `~/.klawde/sessions.db`; the TUI reads from it read-only.

**Captures:** session status, CWD, model, context %, cost (USD equivalent for subscription users), session/api duration, token totals, rate limits (5h/7d), lines added/removed, kitty window/socket, Claude Code version, output style, git worktree, and a full audit event log.

**Schema:** three tables — `sessions` (~25 columns), `session_metadata` (namespaced key-value), `events` (append-only). WAL mode.

**Key files:**
- `metrics/setup.sh` — idempotent installer
- `metrics/session_start.sh` / `session_end.sh` / `notification.sh` / `post_tool_use.sh` — lifecycle hooks
- `metrics/kitty_start.sh` — captures kitty window state into `session_metadata`
- `metrics/statusline.sh` — per-tick metrics UPDATE + emoji-rich output
- `metrics/prune.sh` — retention (events >30d, stopped sessions >90d)

### Composing statuslines

`~/.klawde/statusline.sh` does not chain to any downstream statusline — that's the operator's call. Example wrapper at `~/.claude/custom-status-line.sh`:

```bash
#!/usr/bin/env bash
# klawde + caveman composed statusline.
# caveman reads a flag file (no stdin); klawde inherits parent stdin directly.

CAVEMAN_SL="$HOME/.claude/plugins/marketplaces/caveman/hooks/caveman-statusline.sh"
if [ -f "$CAVEMAN_SL" ]; then
  CAVEMAN_OUT=$(bash "$CAVEMAN_SL")
  if [ -n "$CAVEMAN_OUT" ]; then
    CAVEMAN_OUT="${CAVEMAN_OUT//\[CAVEMAN:/🦴:}"
    CAVEMAN_OUT="${CAVEMAN_OUT//\[CAVEMAN\]/🦴}"
    CAVEMAN_OUT="${CAVEMAN_OUT//]/}"
    printf '%s ┃ ' "$CAVEMAN_OUT"
  fi
fi

"$HOME/.klawde/statusline.sh"
```

Then point `statusLine.command` at `/home/YOU/.claude/custom-status-line.sh`. Any statusline that reads Claude Code's JSON from stdin slots into this pattern.

### Testing

```bash
bash ~/.klawde/test_data.sh                         # seed 4 fake sessions
bash ~/.klawde/simulate_hook.sh SessionStart /path/to/event.json
```

## Development

```bash
cd tui
uv sync --extra dev
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run python -m pytest
```
