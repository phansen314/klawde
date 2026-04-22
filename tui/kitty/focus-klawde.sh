#!/usr/bin/env bash
# Focus the klawde TUI window from any kitty window.
# Wire into kitty.conf — see README for keybinding examples.
# Called via `launch --type=background`, so KITTY_LISTEN_ON is not set;
# socket path is read from the klawde.window registration file instead.
set -euo pipefail

window_file="${XDG_RUNTIME_DIR:-/tmp}/klawde.window"
[[ -f "$window_file" ]] || exit 0

window_id=""
listen_on=""
{ IFS= read -r window_id && IFS= read -r listen_on; } < "$window_file" || :

[[ -z "$window_id" || -z "$listen_on" ]] && exit 0

kitten @ --to "$listen_on" focus-window --match "id:${window_id}"
