#!/usr/bin/env bash
# Seeds ~/.klawde/sessions.db with fake sessions for manual testing.
# Safe to re-run — inserts are INSERT OR REPLACE.
. "$HOME/.klawde/common.sh"

if [ ! -f "$KLAWDE_DB" ]; then
  printf 'DB not found. Run: bash metrics/setup.sh\n' >&2
  exit 1
fi

NOW=$(now)
T1=$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)
T2=$(date -u -d '25 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-25M +%Y-%m-%dT%H:%M:%SZ)
T3=$(date -u -d '3 hours ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-3H +%Y-%m-%dT%H:%M:%SZ)
T4=$(date -u -d '7 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-7M +%Y-%m-%dT%H:%M:%SZ)

SID_A='aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa'
SID_B='bbbbbbbb-0002-0002-0002-bbbbbbbbbbbb'
SID_C='cccccccc-0003-0003-0003-cccccccccccc'
SID_D='dddddddd-0004-0004-0004-dddddddddddd'

db <<EOF
-- v2 schema added events(session_id) FK referencing sessions. INSERT OR
-- REPLACE on sessions would DELETE the conflicting row first, but the FK
-- (no ON DELETE CASCADE) blocks that when children already exist from a
-- prior run. Remove children up-front so the replace lands cleanly.
DELETE FROM events          WHERE session_id IN ('$SID_A','$SID_B','$SID_C','$SID_D');
DELETE FROM session_metadata WHERE session_id IN ('$SID_A','$SID_B','$SID_C','$SID_D');

INSERT OR REPLACE INTO sessions(session_id, cwd, transcript_path, status, model, session_name, context_percent, context_window_size, rate_limit_5h_percent, started_at, updated_at)
VALUES
  ('$SID_A', '/home/user/code/myapp', '/home/user/.claude/projects/myapp/transcript.jsonl',
   'running', 'claude-sonnet-4-6', 'refactor auth', 45, 200000, 32.0, '$T1', '$NOW'),
  ('$SID_B', '/home/user/code/klawde', '/home/user/.claude/projects/klawde/transcript.jsonl',
   'needs_approval', 'claude-opus-4-7', NULL, 31, 200000, 61.0, '$T2', '$NOW'),
  ('$SID_C', '/home/user/code/infra', '/home/user/.claude/projects/infra/transcript.jsonl',
   'stopped', 'claude-sonnet-4-6', 'deploy pipeline', 78, 200000, 88.5, '$T3', '$T3'),
  ('$SID_D', '/home/user/code/api', '/home/user/.claude/projects/api/transcript.jsonl',
   'running', 'claude-sonnet-4-6', NULL, 87, 200000, 15.0, '$T4', '$NOW');

UPDATE sessions SET stopped_at = '$T3' WHERE session_id = '$SID_C';

INSERT OR REPLACE INTO session_metadata(session_id, namespace, key, value, updated_at)
VALUES
  ('$SID_A', 'kitty', 'window_id', '7',                          '$NOW'),
  ('$SID_A', 'kitty', 'listen_on',  'unix:/tmp/kitty-12345',      '$NOW'),
  ('$SID_B', 'kitty', 'window_id', '4',                          '$NOW'),
  ('$SID_B', 'kitty', 'listen_on',  'unix:/tmp/kitty-12345',      '$NOW'),
  ('$SID_D', 'kitty', 'window_id', '11',                         '$NOW'),
  ('$SID_D', 'kitty', 'listen_on',  'unix:/tmp/kitty-12345',      '$NOW');

INSERT INTO events(session_id, event_type, source, timestamp, raw_json)
VALUES
  ('$SID_A', 'start',          'hook', '$T1', '{"session_id":"$SID_A","source":"startup"}'),
  ('$SID_B', 'start',          'hook', '$T2', '{"session_id":"$SID_B","source":"startup"}'),
  ('$SID_B', 'needs_approval', 'hook', '$NOW','{"session_id":"$SID_B","notification_type":"permission_prompt"}'),
  ('$SID_C', 'start',          'hook', '$T3', '{"session_id":"$SID_C","source":"startup"}'),
  ('$SID_C', 'stop',           'hook', '$T3', '{"session_id":"$SID_C","reason":"logout"}'),
  ('$SID_D', 'start',          'hook', '$T4', '{"session_id":"$SID_D","source":"startup"}');
EOF

printf '==> sessions:\n'
db -column -header \
  "SELECT session_id, status, context_percent, model, session_name FROM sessions;"

printf '\n==> session_metadata (kitty):\n'
db -column -header \
  "SELECT session_id, namespace, key, value FROM session_metadata ORDER BY session_id, key;"

printf '\n==> events (count by type):\n'
db -column -header \
  "SELECT event_type, count(*) as n FROM events GROUP BY event_type ORDER BY n DESC;"
