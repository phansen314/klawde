#!/usr/bin/env bash
set +e
event="$1"
input=$(cat)
events_file="${CLAUDE_SESSION_EVENTS:-$HOME/.claude/session-events.jsonl}"
mkdir -p "$(dirname "$events_file")"
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
session_id=$(jq -r '.session_id // empty' <<<"$input")
if [[ -z "$session_id" ]]; then
  exit 0
fi
if [[ "$event" == "start" ]]; then
  cwd=$(jq -r '.cwd // empty' <<<"$input")
  source=$(jq -r '.source // empty' <<<"$input")
  kwid="${KITTY_WINDOW_ID:-}"
  jq -cn --arg e start --arg s "$session_id" --arg c "$cwd" --arg t "$ts" --arg k "$kwid" --arg src "$source" \
    '{event:$e, session_id:$s, cwd:$c, kitty_window_id:(if $k=="" then null else ($k|tonumber) end), source:(if $src=="" then null else $src end), timestamp:$t}' \
    >> "$events_file"
elif [[ "$event" == "stop" ]]; then
  jq -cn --arg e stop --arg s "$session_id" --arg t "$ts" \
    '{event:$e, session_id:$s, timestamp:$t}' \
    >> "$events_file"
fi
exit 0
