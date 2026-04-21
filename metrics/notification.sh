#!/usr/bin/env bash
. "$HOME/.klawde/common.sh"

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SID" ] && exit 0

# Claude Code fires `Notification` for several types — we only want the
# permission-prompt variant. `idle_prompt` ("Claude is waiting for your input")
# fires when Claude finishes a turn and goes idle; treating it as
# needs_approval leaves sessions stuck in ⏸ after normal turns.
NOTIF_TYPE=$(printf '%s' "$INPUT" | jq -r '.notification_type // empty' 2>/dev/null)
MESSAGE=$(printf '%s' "$INPUT" | jq -r '.message // empty' 2>/dev/null)
case "$NOTIF_TYPE" in
  permission_prompt) ;;
  *)
    # Fallback for notifications without `notification_type`: match the old
    # tui/hooks/emit-session-event.sh heuristic — look for "permission" in
    # the message.
    case "$MESSAGE" in
      *permission*|*Permission*) ;;
      *) exit 0 ;;
    esac
    ;;
esac

TS=$(now)
sq_to     SID_Q "$SID"
sq_to     TS_Q  "$TS"
sq_raw_to RAW_Q "$INPUT"

# Skip event insert if session row is missing (FK would fail). UPDATE NOP
# guarantees changes()==0 in that case; `WHERE changes() > 0` suppresses both
# duplicate-state writes and FK violations. `changes()` of final statement is
# captured via a SELECT so the flag-file touch below can see the state change.
RESULT=$(printf '%s' "BEGIN;
UPDATE sessions
SET status = 'needs_approval', updated_at = $TS_Q
WHERE session_id = $SID_Q AND status != 'needs_approval';
INSERT INTO events(session_id, event_type, source, timestamp, raw_json)
SELECT $SID_Q, 'needs_approval', 'hook', $TS_Q, $RAW_Q
WHERE changes() > 0;
SELECT changes();
COMMIT;" | db)

# Touch flag file when the event insert actually happened (row transitioned
# into needs_approval). post_tool_use.sh reads this file to decide whether to
# fork sqlite3 at all — the common "no approval pending" path stays fork-free.
if [ "$RESULT" != "0" ] && [ -n "$RESULT" ]; then
  : > "$(approval_flag_path "$SID")" 2>/dev/null
fi

exit 0
