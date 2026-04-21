#!/usr/bin/env bash
# Usage: simulate_hook.sh <SessionStart|SessionEnd|Notification|PostToolUse> <json_file>
# Pipes the JSON file to the appropriate hook script, then prints the resulting DB state.
. "$HOME/.klawde/common.sh"

HOOK="$1"
JSON_FILE="$2"
KLAWDE_DIR="$HOME/.klawde"

if [ -z "$HOOK" ] || [ -z "$JSON_FILE" ]; then
  printf 'Usage: simulate_hook.sh <SessionStart|SessionEnd|Notification|PostToolUse> <json_file>\n' >&2
  exit 1
fi

if [ ! -f "$JSON_FILE" ]; then
  printf 'Error: file not found: %s\n' "$JSON_FILE" >&2
  exit 1
fi

case "$HOOK" in
  SessionStart)  SCRIPT="$KLAWDE_DIR/session_start.sh" ;;
  SessionEnd)    SCRIPT="$KLAWDE_DIR/session_end.sh" ;;
  Notification)  SCRIPT="$KLAWDE_DIR/notification.sh" ;;
  PostToolUse)   SCRIPT="$KLAWDE_DIR/post_tool_use.sh" ;;
  *)
    printf 'Unknown hook: %s\n' "$HOOK" >&2
    printf 'Valid hooks: SessionStart, SessionEnd, Notification, PostToolUse\n' >&2
    exit 1
    ;;
esac

if [ ! -x "$SCRIPT" ]; then
  printf 'Hook script not found or not executable: %s\n' "$SCRIPT" >&2
  printf 'Run: bash metrics/setup.sh\n' >&2
  exit 1
fi

printf '==> Running %s with %s\n' "$HOOK" "$JSON_FILE"
bash "$SCRIPT" < "$JSON_FILE"

printf '\n==> sessions (latest 5):\n'
db -column -header \
  "SELECT session_id, status, context_percent, model, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 5;"

printf '\n==> events (latest 5):\n'
db -column -header \
  "SELECT id, session_id, event_type, source, timestamp FROM events ORDER BY id DESC LIMIT 5;"
