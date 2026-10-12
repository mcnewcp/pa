#!/usr/bin/env bash
# Mounts the store into the PA VM: L0 and the claims graph read-only, enforced on the host so even
# root in the VM can't write them, and the VM's own inbox directory writable, its only way back.
# Nothing else of the data root goes in. Safe to re-run; a device whose settings changed is
# replaced.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -d $PA_L0 ]] || die "$PA_DATA_ROOT has no L0: run seed-data-root.sh first"
# The shares keep owners as they are, with no mapping: the VM's assistant user can write the
# inbox directory only because its uid is the devbox user's.
vm_uid=$(incus exec "$PA_VM" -- id -u "$PA_USER")
[[ $vm_uid == "$(id -u)" ]] ||
  die "$PA_USER is uid $vm_uid in $PA_VM but you are uid $(id -u): it couldn't write its inbox"

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

# The graph directory is shared before the first build, so the VM's mount is there from the
# start; the PA MCP server says no graph is published until `pa librarian rebuild` runs.
mkdir -p "$PA_GRAPH"
for entry in "${PA_SHARES[@]}"; do
  IFS='|' read -r device source path readonly <<<"$entry"
  share "$device" "$source" "$path" "$readonly"
done

# The VM's agent mounts a share when it's added and at every boot.
for entry in "${PA_SHARES[@]}"; do
  IFS='|' read -r _ _ path _ <<<"$entry"
  for _ in $(seq 15); do
    incus exec "$PA_VM" -- mountpoint -q "$path" && continue 2
    sleep 1
  done
  die "$path isn't mounted in $PA_VM"
done
