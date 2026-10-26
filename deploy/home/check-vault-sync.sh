#!/usr/bin/env bash
# Checks the vault copy on the devbox (#87): the headless Sync client is at its pinned version on
# the pinned Node, set up pull-only, running as a service; no VM is shared the copy or the
# client's state; and the daily notes are arriving. It reports the copy by file counts and times
# only, never a name or a line of a note, and never reads the client's log or the journal, which
# name notes. Exits non-zero if any check fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }

node=$PA_VAULT_SYNC_DIR/node/bin/node
client=$PA_VAULT_SYNC_DIR/node_modules/obsidian-headless/cli.js
runner=$PA_VAULT_SYNC_DIR/bin/pa-vault-sync

echo "The client is pinned:"
if [[ $("$node" --version 2>/dev/null || true) == "v$PA_NODE_VERSION" ]]; then
  pass "Node $PA_NODE_VERSION"
else
  fail "Node in $PA_VAULT_SYNC_DIR isn't $PA_NODE_VERSION (run install-vault-sync.sh)"
fi
if [[ $("$node" "$client" --version 2>/dev/null || true) == "$PA_VAULT_SYNC_VERSION" ]]; then
  pass "obsidian-headless $PA_VAULT_SYNC_VERSION"
else
  fail "the client isn't obsidian-headless $PA_VAULT_SYNC_VERSION (run install-vault-sync.sh)"
fi

echo "The vault copy syncs pull-only, as a service:"
if "$runner" check "$PA_VAULT" >/dev/null 2>&1; then
  pass "$PA_VAULT syncs pull-only: nothing written here reaches the owner's devices"
else
  fail "$("$runner" check "$PA_VAULT" 2>&1 >/dev/null || true)"
fi
if systemctl is-enabled --quiet "$PA_VAULT_SYNC_UNIT"; then
  pass "$PA_VAULT_SYNC_UNIT starts at boot"
else
  fail "$PA_VAULT_SYNC_UNIT isn't enabled (install-vault-sync.sh --enable)"
fi
state=$(systemctl show "$PA_VAULT_SYNC_UNIT" -p ActiveState --value)
since=$(systemctl show "$PA_VAULT_SYNC_UNIT" -p ActiveEnterTimestamp --value)
restarts=$(systemctl show "$PA_VAULT_SYNC_UNIT" -p NRestarts --value)
if [[ $state == active ]]; then
  pass "$PA_VAULT_SYNC_UNIT is running, since $since, after $restarts restarts"
else
  fail "$PA_VAULT_SYNC_UNIT is $state, after $restarts restarts (the owner reads journalctl -u $PA_VAULT_SYNC_UNIT)"
fi
expected="$runner run $PA_VAULT"
if systemctl show "$PA_VAULT_SYNC_UNIT" -p ExecStart --value | grep -F "argv[]=$expected ;" >/dev/null; then
  pass "it runs $expected"
else
  fail "it doesn't run $expected (run install-vault-sync.sh)"
fi

echo "The copy and the client's state are the devbox user's, and no VM is shared them:"
for dir in "$PA_VAULT" "$PA_VAULT_SYNC_CONFIG"; do
  if [[ $(stat -c '%U %a' "$dir" 2>/dev/null) == "$USER 700" ]]; then
    pass "$dir is $USER's, mode 0700"
  else
    fail "$dir isn't $USER's with mode 0700 (run install-vault-sync.sh)"
  fi
done
problems=$(incus query '/1.0/instances?recursion=1' | vault_share_problems)
if [[ -z $problems ]]; then
  pass "no VM is shared $PA_VAULT or $PA_VAULT_SYNC_CONFIG"
else
  while read -r problem; do fail "$problem"; done <<<"$problems"
fi

echo "The daily notes are arriving (counts and times only):"
if [[ ! -f $PA_VAULT_ENV ]]; then
  fail "$PA_VAULT_ENV is missing (the wizard's sync stage writes it)"
else
  # shellcheck source=/dev/null # vault.env, written by the wizard
  source "$PA_VAULT_ENV"
  if [[ ${PA_VAULT_DIR:-} != "$PA_VAULT" ]]; then
    fail "$PA_VAULT_ENV doesn't point PA_VAULT_DIR at $PA_VAULT"
  fi
  daily=$PA_VAULT/${PA_VAULT_DAILY_NOTES:-}
  if [[ -z ${PA_VAULT_DAILY_NOTES:-} || ! -d $daily ]]; then
    fail "the daily-notes folder PA_VAULT_DAILY_NOTES names isn't in the copy (yet?)"
  else
    # The newest note's change time, and the note count, as the vault collector reads them.
    read -r count newest < <(find "$daily" -type f -name '*.md' -not -path '*/.*' -printf '%T@\n' |
      sort -n | awk '{n++; last=$1} END {printf "%d %d\n", n, last}')
    total=$(find "$PA_VAULT" -type f -not -path '*/.*' | wc -l)
    if ((count > 0)); then
      pass "$count daily notes of $total files; the newest changed $(date -d "@$newest" '+%F %T %Z'), $((($(date +%s) - newest) / 60)) minutes ago"
    else
      fail "no daily notes in the daily-notes folder ($total files in the copy)"
    fi
  fi
fi

exit "$failed"
