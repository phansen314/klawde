#!/usr/bin/env bash
# Hot path — fires on every tool call. Must stay fork-light.
#   Common case (no approval pending): jq + [ -e flag ] = 1 fork, no DB.
#   Rare case (clearing approval):    full DB UPDATE + flag removal.
. "$HOME/.klawde/common.sh"

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SID" ] && exit 0

FLAG="$(approval_flag_path "$SID")"
# Fast path: no approval pending → exit without sqlite3 fork.
[ ! -e "$FLAG" ] && exit 0

TS=$(now)
sq_to SID_Q "$SID"
sq_to TS_Q  "$TS"

# raw_json is NOT stored — post_tool_use fires on every tool call.
printf '%s' "BEGIN;
UPDATE sessions
SET status = 'running', updated_at = $TS_Q
WHERE session_id = $SID_Q AND status = 'needs_approval';
INSERT INTO events(session_id, event_type, source, timestamp)
SELECT $SID_Q, 'working', 'hook', $TS_Q
WHERE changes() > 0;
COMMIT;" | db

# Clear flag regardless of UPDATE outcome — if row was already 'running' we
# still want the flag gone so future tool calls stay on the fast path.
rm -f "$FLAG" 2>/dev/null

exit 0
