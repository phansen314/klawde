#!/usr/bin/env bash
# Reads Claude Code statusline JSON from stdin, writes metrics to SQLite,
# prints an emoji-rich summary line, and exits. Chaining to another
# statusline is the operator's call — compose in ~/.claude/settings.json.
# README.md has worked examples.
. "$HOME/.klawde/common.sh"

INPUT=$(cat)

# Field separator is ASCII Unit Separator (\x1f). Tab would seem natural here
# but bash treats tab as IFS whitespace and collapses runs, so an empty field
# (e.g. absent session_name) would silently shift every later field left by
# one. US is non-whitespace, so read preserves empty fields positionally.
IFS=$'\x1f' read -r SID MODEL SNAME CTX_PCT CTX_SIZE RL5H_PCT RL5H_RESETS_RAW RL7D_PCT RL7D_RESETS_RAW COST OCWD API_MS LINES_ADD LINES_DEL TOT_IN TOT_OUT VER GWT EXC_200K OSTYLE CWD_JSON <<EOF
$(printf '%s' "$INPUT" | jq -rj '[
  .session_id,
  .model.id,
  .session_name,
  .context_window.used_percentage,
  .context_window.context_window_size,
  .rate_limits.five_hour.used_percentage,
  .rate_limits.five_hour.resets_at,
  .rate_limits.seven_day.used_percentage,
  .rate_limits.seven_day.resets_at,
  .cost.total_cost_usd,
  .worktree.original_cwd,
  .cost.total_api_duration_ms,
  .cost.total_lines_added,
  .cost.total_lines_removed,
  .context_window.total_input_tokens,
  .context_window.total_output_tokens,
  .version,
  .workspace.git_worktree,
  (if .exceeds_200k_tokens then 1 else 0 end),
  .output_style.name,
  .cwd
] | map(. // "" | tostring) | join("\u001f")' 2>/dev/null)
EOF

if [ -n "$SID" ]; then
  # Per-session diff cache: skip UPDATE (and fork) when nothing changed since
  # last render. Cache file lives in /run (tmpfs), one per session. Mismatched
  # fingerprint triggers a write; identical fingerprint short-circuits.
  # stline_cache_path_to sets CACHE to "" on malformed SID → downstream file
  # ops no-op, bypassing cache entirely.
  stline_cache_path_to CACHE "$SID"
  # Fingerprint uses raw epoch seconds rather than ISO. SQLite converts to ISO
  # via strftime(..., 'unixepoch') in the UPDATE, saving two `date` forks per
  # cache miss and keeping change-detection functionally identical.
  FP="$MODEL|$SNAME|$CTX_PCT|$CTX_SIZE|$RL5H_PCT|$RL5H_RESETS_RAW|$RL7D_PCT|$RL7D_RESETS_RAW|$COST|$OCWD|$API_MS|$LINES_ADD|$LINES_DEL|$TOT_IN|$TOT_OUT|$VER|$GWT|$EXC_200K|$OSTYLE"
  PREV=""
  [ -f "$CACHE" ] && IFS= read -r PREV < "$CACHE"

  if [ "$FP" != "$PREV" ]; then
    sq_to          SID_Q         "$SID"
    sq_or_null_to  MODEL_Q       "$MODEL"
    sq_or_null_to  SNAME_Q       "$SNAME"
    num_or_null_to CTX_PCT_V     "$CTX_PCT"
    num_or_null_to CTX_SIZE_V    "$CTX_SIZE"
    num_or_null_to RL5H_PCT_V    "$RL5H_PCT"
    num_or_null_to RL7D_PCT_V    "$RL7D_PCT"
    num_or_null_to RL5H_RESETS_V "$RL5H_RESETS_RAW"
    num_or_null_to RL7D_RESETS_V "$RL7D_RESETS_RAW"
    num_or_null_to COST_V        "$COST"
    sq_or_null_to  OCWD_Q        "$OCWD"
    num_or_null_to API_MS_V      "$API_MS"
    num_or_null_to LINES_ADD_V   "$LINES_ADD"
    num_or_null_to LINES_DEL_V   "$LINES_DEL"
    num_or_null_to TOT_IN_V      "$TOT_IN"
    num_or_null_to TOT_OUT_V     "$TOT_OUT"
    sq_or_null_to  VER_Q         "$VER"
    sq_or_null_to  GWT_Q         "$GWT"
    num_or_null_to EXC_200K_V    "$EXC_200K"
    sq_or_null_to  OSTYLE_Q      "$OSTYLE"

    # UPDATE only — do not resurrect stopped sessions or create rows with empty
    # cwd. Session rows are owned by session_start.sh; if it never fired, we
    # forgo statusline metrics rather than invent a half-baked row.
    # context_update events are NOT logged (fires every statusline tick; current
    # values already live in the sessions row).
    # updated_at and *_resets_at are computed server-side via strftime; strftime
    # returns NULL when its time argument is NULL, so COALESCE falls through and
    # preserves the existing column value when no new epoch was provided.
    printf '%s' "UPDATE sessions SET
  model                   = COALESCE($MODEL_Q, model),
  session_name            = COALESCE($SNAME_Q, session_name),
  context_percent         = COALESCE($CTX_PCT_V, context_percent),
  context_window_size     = COALESCE($CTX_SIZE_V, context_window_size),
  rate_limit_5h_percent   = COALESCE($RL5H_PCT_V, rate_limit_5h_percent),
  rate_limit_5h_resets_at = COALESCE(strftime('%Y-%m-%dT%H:%M:%SZ', $RL5H_RESETS_V, 'unixepoch'), rate_limit_5h_resets_at),
  rate_limit_7d_percent   = COALESCE($RL7D_PCT_V, rate_limit_7d_percent),
  rate_limit_7d_resets_at = COALESCE(strftime('%Y-%m-%dT%H:%M:%SZ', $RL7D_RESETS_V, 'unixepoch'), rate_limit_7d_resets_at),
  total_cost_usd          = COALESCE($COST_V, total_cost_usd),
  original_cwd            = COALESCE($OCWD_Q, original_cwd),
  api_duration_ms         = COALESCE($API_MS_V, api_duration_ms),
  lines_added             = COALESCE($LINES_ADD_V, lines_added),
  lines_removed           = COALESCE($LINES_DEL_V, lines_removed),
  total_input_tokens      = COALESCE($TOT_IN_V, total_input_tokens),
  total_output_tokens     = COALESCE($TOT_OUT_V, total_output_tokens),
  claude_code_version     = COALESCE($VER_Q, claude_code_version),
  git_worktree            = COALESCE($GWT_Q, git_worktree),
  exceeds_200k_tokens     = COALESCE($EXC_200K_V, exceeds_200k_tokens),
  output_style            = COALESCE($OSTYLE_Q, output_style),
  updated_at              = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE session_id = $SID_Q;" | db

    # Update cache atomically (tempfile + rename) only if DB write succeeded
    # AND stline_cache_path returned a non-empty path (malformed SID → skip
    # cache; without this guard `$CACHE.tmp.$$` would write into CWD).
    # Non-atomic write could leave a partial fingerprint for an overlapping
    # tick to read, producing a false cache-miss.
    if [ $? -eq 0 ] && [ -n "$CACHE" ]; then
      TMP="$CACHE.tmp.$$"
      printf '%s\n' "$FP" > "$TMP" && mv -f "$TMP" "$CACHE"
    fi
  fi
fi

# ---- Output ---------------------------------------------------------------
# Two-line emoji summary.
#   L1: 🧠 ctx | 📁 cwd | 🌿 branch | 🤖 model | 💰 cost | ⌛ api-duration
#   L2: ⏱️ 5h N% resets T | 7d N% resets M/D T
# Each segment hides when its data is missing; line 2 collapses when empty.
# Rendered unconditionally (no SID → still display). Bash 3.2-safe.

LINE1=()
LINE2=()

# 🧠 Context — always renders, defaults to 0.
CTX_PCT_INT=0
if [[ "$CTX_PCT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
  printf -v CTX_PCT_INT '%.0f' "$CTX_PCT"
fi
LINE1+=("🧠 ${CTX_PCT_INT}%")

# 📁 Folder — basename of the JSON-provided cwd.
[ -n "$CWD_JSON" ] && LINE1+=("📁 ${CWD_JSON##*/}")

# 🌿 Branch — pure-bash walk up from cwd looking for .git/ (regular repo) or
# .git file (linked worktree). Zero forks.
_branch=""
if [ -n "$CWD_JSON" ]; then
  _dir="$CWD_JSON"
  while [ -n "$_dir" ] && [ "$_dir" != "/" ]; do
    if [ -f "$_dir/.git/HEAD" ]; then
      IFS= read -r _head < "$_dir/.git/HEAD"
      break
    elif [ -f "$_dir/.git" ]; then
      IFS= read -r _gitfile < "$_dir/.git"
      _gitdir="${_gitfile#gitdir: }"
      [ -f "$_gitdir/HEAD" ] && IFS= read -r _head < "$_gitdir/HEAD"
      break
    fi
    _dir="${_dir%/*}"
  done
  case "$_head" in
    "ref: refs/heads/"*) _branch="${_head#ref: refs/heads/}" ;;
    ?*)                  _branch="${_head:0:7}" ;;   # detached: short sha
  esac
fi
[ -n "$_branch" ] && LINE1+=("🌿 $_branch")

# ⏱️ 5h rate limit. Reset time local TZ, 12-hour, no date (always today-ish
# since the window is 5 hours). GNU `%-I` strips zero-pad; BSD gets padded.
if [[ "$RL5H_PCT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
  printf -v _5h_int '%.0f' "$RL5H_PCT"
  _seg="⏱️ 5h ${_5h_int}%"
  if [ -n "$RL5H_RESETS_RAW" ]; then
    _t=$(date -d "@$RL5H_RESETS_RAW" +'%-I:%M%p' 2>/dev/null \
         || date -r "$RL5H_RESETS_RAW" +'%I:%M%p' 2>/dev/null)
    [ -n "$_t" ] && _seg+=" resets $_t"
  fi
  LINE2+=("$_seg")
fi

# 7d rate limit. Reset time includes M/D since the window spans days.
if [[ "$RL7D_PCT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
  printf -v _7d_int '%.0f' "$RL7D_PCT"
  _seg="7d ${_7d_int}%"
  if [ -n "$RL7D_RESETS_RAW" ]; then
    _t=$(date -d "@$RL7D_RESETS_RAW" +'%-m/%-d %-I:%M%p' 2>/dev/null \
         || date -r "$RL7D_RESETS_RAW" +'%m/%d %I:%M%p' 2>/dev/null)
    [ -n "$_t" ] && _seg+=" resets $_t"
  fi
  LINE2+=("$_seg")
fi

# 🤖 Model — family title-cased via hardcoded case; unknown families fall
# through as lowercase (add a branch when a new family ships).
if [ -n "$MODEL" ]; then
  _rest="${MODEL#claude-}"
  case "$_rest" in
    *-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) _rest="${_rest%-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]}" ;;
  esac
  _family="${_rest%%-*}"
  _ver="${_rest#*-}"
  _ver="${_ver//-/.}"
  case "$_family" in
    opus)   _family=Opus ;;
    sonnet) _family=Sonnet ;;
    haiku)  _family=Haiku ;;
  esac
  LINE1+=("🤖 $_family $_ver")
fi

# 💰 Cost — always 2 dp.
if [[ "$COST" =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then
  printf -v _cost_fmt '$%.2f' "$COST"
  LINE1+=("💰 $_cost_fmt")
fi

# ⌛ API duration — cost.total_api_duration_ms → HhMm or Mm. Pure arithmetic,
# no forks. Hidden when 0 or absent.
if [[ "$API_MS" =~ ^[0-9]+$ ]] && [ "$API_MS" -gt 0 ]; then
  _mins=$(( API_MS / 60000 ))
  _hrs=$(( _mins / 60 ))
  _rmin=$(( _mins % 60 ))
  if [ "$_hrs" -gt 0 ]; then
    _apifmt="${_hrs}h${_rmin}m"
  else
    _apifmt="${_mins}m"
  fi
  LINE1+=("⌛ $_apifmt API")
fi

# ---- Assemble with " | " joiners, skip empty lines ------------------------
_join_line_to() {
  local __outvar="$1"; shift
  local out="" s
  for s in "$@"; do
    [ -n "$out" ] && out+=" | "
    out+="$s"
  done
  printf -v "$__outvar" '%s' "$out"
}

_join_line_to L1 "${LINE1[@]}"
_join_line_to L2 "${LINE2[@]}"

OUT="$L1"
[ -n "$L2" ] && OUT+=$'\n'"$L2"
printf '%s' "$OUT"

exit 0
