#!/usr/bin/env bash
# Checks the store's mounts from inside the PA VM: L0 and the wiki are readable where the rendered
# agent project says they are and unwritable even by root, and the VM's inbox directory is the
# only part of the data root it can write; on the devbox, that inbox directory is a filesystem of
# its own that nothing written into it can run from, and the VM is shared exactly those three
# directories. No VM is shared the vault copy or the headless Sync client's state (#87). Exits
# non-zero if any check fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; failed=1; }

# in_vm_as USER COMMAND... runs COMMAND in the VM as USER, quietly; its status is the result.
in_vm_as() {
  local user=$1
  shift
  incus exec "$PA_VM" -- runuser -u "$user" -- "$@" >/dev/null 2>&1
}

echo "In $PA_VM, L0 is readable at the rendered path:"
if incus exec "$PA_VM" -- grep -qF "\`$PA_VM_L0\`" "$PA_VM_PROJECT/CLAUDE.md" 2>/dev/null; then
  pass "$PA_VM_PROJECT/CLAUDE.md points the assistant at $PA_VM_L0"
else
  fail "$PA_VM_PROJECT/CLAUDE.md doesn't name $PA_VM_L0 (run update.sh)"
fi
episode=$(incus exec "$PA_VM" -- find "$PA_VM_L0/episodes" -name '*.json' -print -quit 2>/dev/null || true)
if [[ -n $episode ]] && in_vm_as "$PA_USER" cat "$episode"; then
  pass "$PA_USER reads episodes under $PA_VM_L0"
else
  fail "$PA_USER can't read an episode under $PA_VM_L0"
fi

echo "In $PA_VM, L0 can't be written, even as root:"
for user in "$PA_USER" root; do
  if in_vm_as "$user" touch "$PA_VM_L0/.write-check"; then
    fail "$user created a file in $PA_VM_L0"
    incus exec "$PA_VM" -- rm -f "$PA_VM_L0/.write-check"
  else
    pass "$user can't create a file in $PA_VM_L0"
  fi
  # shellcheck disable=SC2016 # $1 is expanded by the shell in the VM
  if [[ -n $episode ]] && in_vm_as "$user" sh -c ': >>"$1"' sh "$episode"; then
    fail "$user opened $episode for writing"
  else
    pass "$user can't change an episode"
  fi
done

echo "In $PA_VM, the wiki is readable at the rendered path, once there is one:"
# As render-agent-project.sh decides: a wiki is there once the librarian has written its index.
names_wiki() {
  incus exec "$PA_VM" -- grep -qF "\`$PA_VM_WIKI\`" "$PA_VM_PROJECT/CLAUDE.md" 2>/dev/null
}
if ! incus exec "$PA_VM" -- test -f "$PA_VM_WIKI/index.md" 2>/dev/null; then
  if names_wiki; then
    fail "$PA_VM_PROJECT/CLAUDE.md names $PA_VM_WIKI, which holds no wiki (run update.sh)"
  else
    pass "no wiki at $PA_VM_WIKI yet, and the assistant runs on files alone"
  fi
elif names_wiki; then
  pass "$PA_VM_PROJECT/CLAUDE.md points the assistant at $PA_VM_WIKI"
else
  fail "$PA_VM_PROJECT/CLAUDE.md doesn't name $PA_VM_WIKI (run update.sh)"
fi
page=$(incus exec "$PA_VM" -- find "$PA_VM_WIKI" -name '*.md' -print -quit 2>/dev/null || true)
if [[ -z $page ]]; then
  printf '  SKIP  %s\n' "the wiki has no pages yet: run pa librarian run on the devbox"
elif in_vm_as "$PA_USER" cat "$page"; then
  pass "$PA_USER reads pages under $PA_VM_WIKI"
else
  fail "$PA_USER can't read a page under $PA_VM_WIKI"
fi

echo "In $PA_VM, the wiki can't be written, even as root:"
for user in "$PA_USER" root; do
  if in_vm_as "$user" touch "$PA_VM_WIKI/.write-check"; then
    fail "$user created a file in $PA_VM_WIKI"
    incus exec "$PA_VM" -- rm -f "$PA_VM_WIKI/.write-check"
  else
    pass "$user can't create a file in $PA_VM_WIKI"
  fi
  [[ -n $page ]] || continue
  # shellcheck disable=SC2016 # $1 is expanded by the shell in the VM
  if in_vm_as "$user" sh -c ': >>"$1"' sh "$page"; then
    fail "$user opened $page for writing"
  else
    pass "$user can't change a page"
  fi
done

echo "In $PA_VM, the inbox directory is writable, and it reaches the devbox:"
probe=.store-check-$$
if in_vm_as "$PA_USER" touch "$PA_VM_CHAT_INBOX/$probe" &&
  [[ -e $PA_CHAT_INBOX/$probe ]]; then
  pass "$PA_USER's file in $PA_VM_CHAT_INBOX lands in $PA_CHAT_INBOX"
else
  fail "$PA_USER's file in $PA_VM_CHAT_INBOX doesn't reach the devbox"
fi
rm -f "$PA_CHAT_INBOX/$probe"

echo "On the devbox, the VM's inbox directory is a filesystem of its own:"
if missing=$(chat_inbox_missing_flags); then
  for flag in "${PA_CHAT_INBOX_FLAGS[@]}"; do
    if grep -qx "$flag" <<<"$missing"; then
      fail "$PA_CHAT_INBOX is mounted without $flag"
    else
      pass "$PA_CHAT_INBOX is mounted $flag"
    fi
  done
  size=$(findmnt -rnb -M "$PA_CHAT_INBOX" -o SIZE)
  if ((size <= $(numfmt --from=iec "$PA_CHAT_INBOX_SIZE"))); then
    pass "$PA_CHAT_INBOX holds at most $PA_CHAT_INBOX_SIZE"
  else
    fail "$PA_CHAT_INBOX holds $(numfmt --to=iec "$size"), more than $PA_CHAT_INBOX_SIZE"
  fi
else
  fail "$PA_CHAT_INBOX isn't a mount of its own (run install-chat-inbox.sh)"
fi

echo "The VM is shared L0, the inbox directory and the wiki, and nothing else:"
problems=$(incus query "/1.0/instances/$PA_VM" | store_share_problems)
if [[ -z $problems ]]; then
  pass "the devbox shares L0 and the wiki read-only, inbox/assistant_chat, and nothing else"
else
  while read -r problem; do fail "$problem"; done <<<"$problems"
fi
expected=$(
  for entry in "${PA_SHARES[@]}"; do
    IFS='|' read -r _ _ path _ <<<"$entry"
    echo "$path"
  done | sort
)
mounts=$(incus exec "$PA_VM" -- findmnt -rn -t virtiofs -o TARGET | sort)
if [[ $mounts == "$expected" ]]; then
  pass "the VM mounts only $(paste -sd ' ' <<<"$expected")"
else
  fail "the VM mounts other shares:"$'\n'"$mounts"
fi

echo "No VM is shared the vault copy or the headless Sync client's state:"
problems=$(incus query '/1.0/instances?recursion=1' | vault_share_problems)
if [[ -z $problems ]]; then
  pass "no VM is shared $PA_VAULT or $PA_VAULT_SYNC_CONFIG, or a directory holding them"
else
  while read -r problem; do fail "$problem"; done <<<"$problems"
fi

exit "$failed"
