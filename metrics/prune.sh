#!/usr/bin/env bash
# Retention prune — deletes events older than 30 days and stopped sessions
# (with their metadata) older than 90 days. Safe to re-run; idempotent.
# Invoked automatically by setup.sh; also runnable standalone.
. "$HOME/.klawde/common.sh"

[ ! -f "$KLAWDE_DB" ] && exit 0

# strftime emits the same `YYYY-MM-DDTHH:MM:SSZ` shape as our `now()` output,
# so a plain string comparison against `timestamp` / `stopped_at` works.
db <<'ENDSQL'
BEGIN;
-- Zombie reap: active sessions that haven't ticked for >4h. Covers machine
-- suspend/crash where session_end.sh never fired. statusline bumps
-- updated_at on every render, so a gap this wide means the session is gone.
UPDATE sessions
   SET status     = 'stopped',
       stopped_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
 WHERE status IN ('running', 'needs_approval')
   AND updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-4 hours');

DELETE FROM events
 WHERE timestamp < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days');

DELETE FROM session_metadata
 WHERE session_id IN (
   SELECT session_id FROM sessions
    WHERE status = 'stopped'
      AND stopped_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-90 days')
 );

DELETE FROM sessions
 WHERE status = 'stopped'
   AND stopped_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-90 days');
COMMIT;

-- Return freelist pages to the OS. No-op on DBs still at auto_vacuum=0
-- (pre-v3 installs that never ran the setup.sh migration).
PRAGMA incremental_vacuum;
ENDSQL
