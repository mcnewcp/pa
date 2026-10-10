#!/usr/bin/env bash
# Checks the store's mounts from inside the PA VM: L0 is readable where the rendered agent
# project says it is and unwritable even by root, and the VM's inbox directory is the only part
# of the data root it can see or write; on the devbox, that inbox directory is a filesystem of its
# own that nothing written into it can run from. Exits non-zero if any check fails.
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
if options=$(findmnt -rn -M "$PA_CHAT_INBOX" -o OPTIONS); then
  for flag in nosuid nodev noexec; do
    if [[ ,$options, == *,$flag,* ]]; then
      pass "$PA_CHAT_INBOX is mounted $flag"
    else
      fail "$PA_CHAT_INBOX is mounted without $flag"
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

echo "Nothing else of the data root is visible in $PA_VM:"
expected=$(printf '%s\n' "$PA_CHAT_INBOX" "$PA_L0" | sort)
shared=$(incus query "/1.0/instances/$PA_VM" |
  python3 -c 'import json, sys
for device in json.load(sys.stdin)["expanded_devices"].values():
    if device.get("type") == "disk" and device.get("source", "").startswith("/"):
        print(device["source"])' | sort)
if [[ $shared == "$expected" ]]; then
  pass "the VM's only host directories are L0 and inbox/assistant_chat"
else
  fail "the VM shares other host directories:"$'\n'"$shared"
fi
mounts=$(incus exec "$PA_VM" -- findmnt -rn -t virtiofs -o TARGET | sort)
if [[ $mounts == "$(printf '%s\n' "$PA_VM_CHAT_INBOX" "$PA_VM_L0" | sort)" ]]; then
  pass "the VM mounts only $PA_VM_L0 and $PA_VM_CHAT_INBOX"
else
  fail "the VM mounts other shares:"$'\n'"$mounts"
fi

exit "$failed"
