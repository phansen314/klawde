#!/usr/bin/env bash
. "$HOME/.klawde/common.sh"

INPUT=$(cat)
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SID" ] && exit 0

TS=$(now)
sq_to     SID_Q "$SID"
sq_to     TS_Q  "$TS"
sq_raw_to RAW_Q "$INPUT"

# Preserve first stop: skip UPDATE if already stopped, and suppress duplicate stop event.
printf '%s' "BEGIN;
UPDATE sessions
SET status = 'stopped', stopped_at = $TS_Q, updated_at = $TS_Q
WHERE session_id = $SID_Q AND status != 'stopped';
INSERT INTO events(session_id, event_type, source, timestamp, raw_json)
SELECT $SID_Q, 'stop', 'hook', $TS_Q, $RAW_Q
WHERE changes() > 0;
COMMIT;" | db

# Clear per-session tmpfs files: approval flag + statusline diff cache.
# Statusline cache is keyed by session_id and written on every cache miss;
# without this the files accumulate in $KLAWDE_RUNTIME until user logout.
rm -f "$(approval_flag_path "$SID")" "$(stline_cache_path "$SID")" 2>/dev/null

exit 0
