#!/bin/sh
# One-time install. Safe to re-run (idempotent).
# Usage: bash metrics/setup.sh

KLAWDE_DIR="$HOME/.klawde"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Probe required tools. Hooks silently no-op when these are missing (jq: SID
# never parsed → early exit; sqlite3: db() fails but stdout is empty), so a
# missing binary would produce an invisibly-inert metrics layer. Catch it at
# install time where the operator can act.
MISSING=""
command -v jq      >/dev/null 2>&1 || MISSING="$MISSING jq"
command -v sqlite3 >/dev/null 2>&1 || MISSING="$MISSING sqlite3"
if [ -n "$MISSING" ]; then
  printf 'ERROR: missing required tool(s):%s\n' "$MISSING" >&2
  printf 'Install them and re-run: bash metrics/setup.sh\n' >&2
  exit 2
fi

mkdir -p "$KLAWDE_DIR"

# Copy scripts only when invoked from outside ~/.klawde/ (e.g. the repo).
if [ "$SCRIPT_DIR" != "$KLAWDE_DIR" ]; then
  cp -f "$SCRIPT_DIR"/*.sh "$KLAWDE_DIR/"
fi
chmod +x "$KLAWDE_DIR"/*.sh

# Create DB + schema (CREATE TABLE IF NOT EXISTS = safe to re-run).
# Note: synchronous=NORMAL is per-connection and also applied in common.sh db().
sqlite3 "$KLAWDE_DIR/sessions.db" <<'ENDSQL'
-- auto_vacuum MUST be set before the DB is "populated" — in SQLite, a
-- journal_mode change counts as a populate — so it has to come first.
PRAGMA auto_vacuum=INCREMENTAL;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT NOT NULL,
    transcript_path TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    model TEXT,
    session_name TEXT,
    context_percent INTEGER,
    context_window_size INTEGER,
    rate_limit_5h_percent REAL,
    rate_limit_5h_resets_at TEXT,
    rate_limit_7d_percent REAL,
    rate_limit_7d_resets_at TEXT,
    total_cost_usd REAL,
    original_cwd TEXT,
    api_duration_ms INTEGER,
    lines_added INTEGER,
    lines_removed INTEGER,
    total_input_tokens INTEGER,
    total_output_tokens INTEGER,
    claude_code_version TEXT,
    git_worktree TEXT,
    exceeds_200k_tokens INTEGER,
    output_style TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE TABLE IF NOT EXISTS session_metadata (
    session_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, namespace, key),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    raw_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_events_session_ts ON events(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
ENDSQL

# Idempotent column adds for DBs created before these columns existed. SQLite
# lacks `ADD COLUMN IF NOT EXISTS`, so we probe pragma_table_info and gate the
# ALTER. Fresh DBs already have the columns from the CREATE block above, so
# both gates evaluate to false and this block no-ops.
for col_spec in \
  "total_cost_usd REAL" \
  "original_cwd TEXT" \
  "api_duration_ms INTEGER" \
  "lines_added INTEGER" \
  "lines_removed INTEGER" \
  "total_input_tokens INTEGER" \
  "total_output_tokens INTEGER" \
  "claude_code_version TEXT" \
  "git_worktree TEXT" \
  "exceeds_200k_tokens INTEGER" \
  "output_style TEXT"; do
  col_name="${col_spec%% *}"
  exists=$(sqlite3 "$KLAWDE_DIR/sessions.db" \
    "SELECT COUNT(*) FROM pragma_table_info('sessions') WHERE name='$col_name';")
  if [ "$exists" = "0" ]; then
    sqlite3 "$KLAWDE_DIR/sessions.db" "ALTER TABLE sessions ADD COLUMN $col_spec;"
  fi
done

# Schema version stamp. Starts at 1; bump when introducing migration blocks
# below. Fresh DBs get v1 from the CREATE block above (auto_vacuum is set at
# creation time since the PRAGMA precedes any table — no VACUUM conversion
# needed).
sqlite3 "$KLAWDE_DIR/sessions.db" "PRAGMA user_version = 1;"

# Rotate hook error log (keep last 500 lines).
if [ -f "$KLAWDE_DIR/hook-errors.log" ]; then
  LINES=$(wc -l < "$KLAWDE_DIR/hook-errors.log" 2>/dev/null || printf 0)
  if [ "${LINES:-0}" -gt 500 ]; then
    tail -n 500 "$KLAWDE_DIR/hook-errors.log" > "$KLAWDE_DIR/hook-errors.log.tmp" \
      && mv "$KLAWDE_DIR/hook-errors.log.tmp" "$KLAWDE_DIR/hook-errors.log"
  fi
fi

# Retention prune (events >30d, stopped sessions >90d). Best-effort.
bash "$KLAWDE_DIR/prune.sh" 2>/dev/null

printf 'klawde metrics installed to %s\n' "$KLAWDE_DIR"
printf 'DB: %s\n' "$KLAWDE_DIR/sessions.db"
cat <<'BANNER'

  ╔═════════════════════════════════════════════════════════════╗
  ║  ACTION REQUIRED: wire hooks + statusline manually          ║
  ║                                                             ║
  ║  setup.sh does NOT modify ~/.claude/settings.json.          ║
  ║  ~/.klawde/statusline.sh writes metrics + prints [KLAWDE].  ║
  ║  To chain with another statusline (e.g. caveman), compose   ║
  ║  them yourself in settings.json.                            ║
  ║                                                             ║
  ║  See README.md (metrics section) for working examples.      ║
  ╚═════════════════════════════════════════════════════════════╝
BANNER
