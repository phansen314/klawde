#!/usr/bin/env bash
# Shared helpers for klawde metrics hooks.
KLAWDE_DB="$HOME/.klawde/sessions.db"
KLAWDE_ERRLOG="$HOME/.klawde/hook-errors.log"
KLAWDE_RUNTIME="${XDG_RUNTIME_DIR:-/tmp}"

# Out-var SQL quoter: assign to caller-named variable via `printf -v`.
# Avoids the subshell fork that `$(sq ...)` would incur — saves ~6 forks per
# hook invocation across session_start.sh / statusline.sh.
sq_to() {
  local __v="${2//\'/\'\'}"
  printf -v "$1" "'%s'" "$__v"
}

# NULL if empty, quoted otherwise.
sq_or_null_to() {
  if [ -z "$2" ]; then printf -v "$1" 'NULL'; else sq_to "$1" "$2"; fi
}

# Numeric literal when input matches `-?digits(.digits)?`; NULL otherwise.
# Guards against upstream JSON shape changes emitting strings or "null" —
# caller no longer needs to pre-validate.
num_or_null_to() {
  if [[ "$2" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
    printf -v "$1" '%s' "$2"
  else
    printf -v "$1" 'NULL'
  fi
}

# Truncate raw payload to 8KiB then SQL-quote, into caller-named var.
sq_raw_to() {
  local __v="$2"
  [ ${#__v} -gt 8192 ] && __v="${__v:0:8192}...[truncated]"
  sq_to "$1" "$__v"
}

# Reject non-UUID-shaped session_ids. Centralised so every per-session file
# path (approval flag, statusline cache) is gated by the same rule — a SID
# containing `/` or `..` could otherwise let a malformed hook payload escape
# $KLAWDE_RUNTIME. A non-zero return produces empty stdout from the helpers
# below; callers (`rm -f "$(...)"`, `: > "$(...)"`) against an empty string
# silently no-op.
_valid_sid() { [[ "$1" =~ ^[a-zA-Z0-9-]+$ ]]; }

# Path to per-session needs_approval flag file. Presence = session currently
# needs approval; post_tool_use uses this to short-circuit with zero sqlite3
# forks.
approval_flag_path() {
  _valid_sid "$1" || return 1
  printf '%s/klawde-needs-approval-%s' "$KLAWDE_RUNTIME" "$1"
}

# Path to per-session statusline diff cache. Written by statusline.sh on each
# cache miss; removed by session_end.sh.
stline_cache_path() {
  _valid_sid "$1" || return 1
  printf '%s/klawde-stline-%s' "$KLAWDE_RUNTIME" "$1"
}

# Out-var variant — avoids the subshell that `$(stline_cache_path ...)` would
# incur on every statusline tick. Sets caller's var to the empty string when
# the SID is malformed; downstream file ops on "" silently no-op.
stline_cache_path_to() {
  if _valid_sid "$2"; then
    printf -v "$1" '%s/klawde-stline-%s' "$KLAWDE_RUNTIME" "$2"
  else
    printf -v "$1" ''
    return 1
  fi
}

# now() — millisecond-precision ISO-8601 UTC when available (GNU date),
# else seconds with .000Z suffix (BSD date).
__klawde_ms_probe=$(date -u +%3N 2>/dev/null)
case "$__klawde_ms_probe" in
  ''|*[!0-9]*) now() { date -u +%Y-%m-%dT%H:%M:%S.000Z; } ;;
  *)           now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; } ;;
esac
unset __klawde_ms_probe

# unix_to_iso — convert unix epoch seconds to ISO-8601. GNU vs BSD date.
if date -u -d "@0" >/dev/null 2>&1; then
  unix_to_iso() { [ -z "$1" ] && return; date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null; }
else
  unix_to_iso() { [ -z "$1" ] && return; date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null; }
fi

# sqlite3 with busy timeout + FK enforcement + synchronous=NORMAL. Logs stderr
# to hook-errors.log on nonzero exit. Preserves stdout so SELECT callers work.
# errfile path is inline ($$ is PID, unique per hook process) — saves the
# mktemp fork that used to run on every SQL call.
db() {
  local errfile="/tmp/klawde-db-err.$$" rc
  sqlite3 \
    -cmd ".timeout 2000" \
    -cmd "PRAGMA foreign_keys=ON" \
    -cmd "PRAGMA synchronous=NORMAL" \
    "$KLAWDE_DB" "$@" 2>"$errfile"
  rc=$?
  if [ $rc -ne 0 ] && [ -s "$errfile" ]; then
    {
      printf '%s\t%s\trc=%d\t' "$(now)" "${0##*/}" "$rc"
      tr '\n' ' ' < "$errfile"
      printf '\n'
    } >> "$KLAWDE_ERRLOG" 2>/dev/null
  fi
  rm -f "$errfile"
  return $rc
}
