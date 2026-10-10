#!/usr/bin/env bash
# Mounts the store into the PA VM: L0 read-only, enforced on the host so even root in the VM
# can't write it, and the VM's own inbox directory writable, its only way back. Nothing else of
# the data root goes in. Safe to re-run; a device whose settings changed is replaced.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -d $PA_DATA_ROOT/l0 ]] || die "$PA_DATA_ROOT has no L0: run seed-data-root.sh first"

# share DEVICE SOURCE PATH [READONLY] attaches SOURCE at PATH in the VM as a disk device.
share() {
  local device=$1 source=$2 path=$3 readonly=${4:-false}
  if incus config device get "$PA_VM" "$device" source >/dev/null 2>&1; then
    if [[ $(incus config device get "$PA_VM" "$device" source) == "$source" &&
      $(incus config device get "$PA_VM" "$device" path) == "$path" &&
      $(incus config device get "$PA_VM" "$device" readonly) == "$readonly" ]]; then
      log "$device: $source at $path (readonly=$readonly)"
      return
    fi
    incus config device remove "$PA_VM" "$device" >/dev/null
  fi
  incus config device add "$PA_VM" "$device" disk \
    source="$source" path="$path" readonly="$readonly" >/dev/null
  log "$device: mounted $source at $path (readonly=$readonly)"
}

share pa-l0 "$PA_DATA_ROOT/l0" "$PA_VM_L0" true
share pa-chat-inbox "$PA_DATA_ROOT/inbox/assistant_chat" "$PA_VM_CHAT_INBOX"

# The VM's agent mounts a share when it's added and at every boot.
for path in "$PA_VM_L0" "$PA_VM_CHAT_INBOX"; do
  for _ in $(seq 15); do
    incus exec "$PA_VM" -- mountpoint -q "$path" && continue 2
    sleep 1
  done
  die "$path isn't mounted in $PA_VM"
done
