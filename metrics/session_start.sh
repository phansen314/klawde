#!/usr/bin/env bash
. "$HOME/.klawde/common.sh"

INPUT=$(cat)
{ read -r SID; read -r CWD; read -r TRANSCRIPT; read -r MODEL; } <<EOF
$(printf '%s' "$INPUT" | jq -r '.session_id // "", .cwd // "", .transcript_path // "", .model // ""' 2>/dev/null)
EOF
[ -z "$SID" ] && exit 0

TS=$(now)

sq_to         SID_Q        "$SID"
sq_to         CWD_Q        "$CWD"
sq_or_null_to TRANSCRIPT_Q "$TRANSCRIPT"
sq_or_null_to MODEL_Q      "$MODEL"
sq_to         TS_Q         "$TS"
sq_raw_to     RAW_Q        "$INPUT"

SQL="BEGIN;
INSERT INTO sessions(session_id, cwd, transcript_path, status, model, started_at, updated_at)
VALUES($SID_Q, $CWD_Q, $TRANSCRIPT_Q, 'running', $MODEL_Q, $TS_Q, $TS_Q)
ON CONFLICT(session_id) DO UPDATE SET
  cwd             = excluded.cwd,
  transcript_path = excluded.transcript_path,
  status          = CASE WHEN sessions.status = 'needs_approval' THEN 'needs_approval' ELSE 'running' END,
  model           = COALESCE(excluded.model, sessions.model),
  stopped_at      = NULL,
  updated_at      = excluded.updated_at;
INSERT INTO events(session_id, event_type, source, timestamp, raw_json)
VALUES($SID_Q, 'start', 'hook', $TS_Q, $RAW_Q);
COMMIT;"

printf '%s' "$SQL" | db

exit 0
