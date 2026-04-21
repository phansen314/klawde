#!/usr/bin/env bash
# Captures kitty window state into session_metadata on SessionStart.
# Claude Code fires SessionStart hooks concurrently regardless of `async: true`,
# so kitty_start.sh and session_start.sh race. We make this hook order-safe
# with INSERT OR IGNORE on the sessions row (skeleton is no-op when
# session_start.sh has already created the richer row; conversely session_start's
# ON CONFLICT DO UPDATE enriches our skeleton if we get there first).
# No-op outside kitty (KITTY_WINDOW_ID unset).
. "$HOME/.klawde/common.sh"

[ -z "${KITTY_WINDOW_ID:-}" ] && exit 0

INPUT=$(cat)
{ read -r SID; read -r CWD; } <<EOF
$(printf '%s' "$INPUT" | jq -r '.session_id // "", .cwd // ""' 2>/dev/null)
EOF
[ -z "$SID" ] && exit 0

TS=$(now)
sq_to SID_Q "$SID"
sq_to CWD_Q "${CWD:-.}"
sq_to TS_Q  "$TS"
sq_to KW_Q  "$KITTY_WINDOW_ID"
sq_to KL_Q  "${KITTY_LISTEN_ON:-}"

printf '%s' "BEGIN;
INSERT OR IGNORE INTO sessions(session_id, cwd, status, started_at, updated_at)
VALUES($SID_Q, $CWD_Q, 'running', $TS_Q, $TS_Q);
INSERT INTO session_metadata(session_id, namespace, key, value, updated_at)
VALUES($SID_Q, 'kitty', 'window_id', $KW_Q, $TS_Q)
ON CONFLICT(session_id, namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
INSERT INTO session_metadata(session_id, namespace, key, value, updated_at)
VALUES($SID_Q, 'kitty', 'listen_on', $KL_Q, $TS_Q)
ON CONFLICT(session_id, namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
COMMIT;" | db

exit 0
