# klawde

A [Textual](https://textual.textualize.io/) TUI for managing multiple Claude Code sessions in [kitty](https://sw.kovidgoyal.net/kitty/). Shows all live sessions with status, context usage, CWD, model, and duration. Press Enter to jump to that session's kitty window.

```
● | Prompt                        | Ctx        | CWD                | Duration | Model   | Session | Kitty
──┼───────────────────────────────┼────────────┼────────────────────┼──────────┼─────────┼─────────┼──────
⏸ | refactor the auth middleware  | ████░ 62%  | ~/code/myapp       | 00:14:22 | sonnet  | a3f9b2c1| 7
● | fix the flaky test in CI      | ██░░░ 31%  | ~/code/klawde      | 00:03:07 | opus    | 9d1e4f82| 4
```

## Prerequisites

- [kitty terminal](https://sw.kovidgoyal.net/kitty/) with remote control enabled
- [Claude Code](https://claude.ai/code) CLI (`claude`)
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or `brew install uv`)
- `jq` (`apt install jq` / `brew install jq`)

## Installation

### 1. Clone and install

```bash
git clone https://github.com/phansen314/klawde.git
cd klawde
uv sync
```

Or install directly:

```bash
uv tool install git+https://github.com/phansen314/klawde.git
```

### 2. Configure kitty

Add to `~/.config/kitty/kitty.conf`:

```conf
allow_remote_control yes
listen_on unix:/tmp/kitty-{kitty_pid}
```

Restart kitty (or reload config with `ctrl+shift+f5`) for changes to take effect.

> **Why:** klawde uses `kitten @` to focus windows by ID. `allow_remote_control` enables the IPC
> socket; `listen_on` sets the socket path. The `{kitty_pid}` placeholder makes each kitty
> instance use a unique socket so multiple kitty instances don't collide.

### 3. Wire Claude Code hooks

Add the following to `~/.claude/settings.json` (merge with existing `hooks` if present):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/klawde/hooks/emit-session-event.sh start"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/klawde/hooks/emit-session-event.sh stop"
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/klawde/hooks/emit-session-event.sh needs_approval"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/klawde/hooks/emit-session-event.sh working"
          }
        ]
      }
    ]
  }
}
```

Replace `/path/to/klawde` with the actual clone path (e.g. `~/code/klawde`). The hooks emit session lifecycle events to `~/.claude/session-events.jsonl`, which the TUI tails in real time.

### 4. (Optional) Jump back to klawde from any window

klawde writes its own kitty window ID to `${XDG_RUNTIME_DIR:-/tmp}/klawde.window` on startup, so you can bind a hotkey to focus it from anywhere. The script is included at `hooks/focus-klawde.sh`.

```bash
chmod +x /path/to/klawde/kitty/focus-klawde.sh
```

Then bind it in `kitty.conf` — for example, a single keypress:

```conf
map ctrl+shift+0 launch --type=background /path/to/klawde/kitty/focus-klawde.sh
```

Or a chord (`ctrl+space` then `c`):

```conf
map ctrl+space>c launch --type=background /path/to/klawde/kitty/focus-klawde.sh
```

> **Note:** `launch --type=background` drops `KITTY_LISTEN_ON`, so `focus-klawde.sh` reads the
> socket path from `klawde.window` rather than the environment.

## Running

```bash
# From the repo
uv run klawde

# Or if installed as a tool
klawde
```

Point klawde at a different event log (useful for testing):

```bash
CLAUDE_SESSION_EVENTS=/tmp/my-events.jsonl uv run klawde
```

Seed synthetic test data:

```bash
uv run scripts/seed_test_data.py
CLAUDE_SESSION_EVENTS=/tmp/klawde-test/session-events.jsonl uv run klawde
```

## Keybindings

| Key        | Action                              |
|------------|-------------------------------------|
| `Enter`    | Focus the selected session's window |
| `q` / `Q`  | Quit                                |
| `↑` / `↓`  | Navigate sessions                   |

## Environment variables

| Variable               | Default                          | Purpose                        |
|------------------------|----------------------------------|--------------------------------|
| `CLAUDE_SESSION_EVENTS`| `~/.claude/session-events.jsonl` | Path to the event log          |
| `KITTY_LISTEN_ON`      | *(set by kitty)*                 | Socket for remote control      |
| `KITTY_WINDOW_ID`      | *(set by kitty)*                 | Window ID captured at hook time|

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run python -m pytest
```
