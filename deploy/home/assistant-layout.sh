#!/usr/bin/env bash
# Usage: assistant-layout.sh <user> <home> <scratch> <eval dir>
#
# Runs in the PA VM, as root: install-assistant-fences.sh pipes it to `incus exec`. Lays out what
# the assistant writes and what it mustn't (#92):
#
# - scratch, the one directory its sandboxed shell writes, and the evals' directory: the
#   assistant's, mode 0700, outside its home;
# - its user settings: an empty object, root's and mode 0644, since no setting turns user
#   settings off and the home directory is the assistant's. A link in their place is replaced,
#   never written through;
# - auto memory: every project's memory directory under ~/.claude/projects is removed. The
#   managed settings keep new sessions from writing one.
set -euo pipefail
user=$1 home=$2 scratch=$3 eval_dir=$4

for dir in "$scratch" "$eval_dir"; do
  mkdir -p "$dir"
  chmod 0700 "$dir"
  chown "$user:$user" "$dir"
done

claude_dir=$home/.claude
if [[ ! -d $claude_dir ]]; then
  mkdir -m 0700 "$claude_dir"
  chown "$user:$user" "$claude_dir"
fi
# Made beside them under a new name (mktemp creates it, never opens one there) and renamed over
# them, which replaces a link rather than following it.
settings=$claude_dir/settings.json
new=$(mktemp "$claude_dir/.settings.json.XXXXXX")
printf '{}\n' >"$new"
chmod 0644 "$new"
chown root:root "$new"
mv -fT "$new" "$settings"

if [[ -d $claude_dir/projects ]]; then
  find "$claude_dir/projects" -mindepth 2 -maxdepth 2 -name memory \( -type d -o -type l \) \
    -exec rm -rf {} +
fi
