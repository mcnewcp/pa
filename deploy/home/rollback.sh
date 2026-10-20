#!/usr/bin/env bash
# Usage: rollback.sh [--vm assistant|librarian] [snapshot]
#
# Restores a PA VM, by default the assistant's, to a snapshot, by default the latest one update.sh
# took. The librarian's VM is left stopped, as it is between runs.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
take_vm_option "$@"
set -- "${ARGS[@]}"

snapshot=${1:-$(incus query "/1.0/instances/$PA_VM/snapshots" |
  grep -o 'snapshots/pre-update-[^"]*' | sed 's|^snapshots/||' | sort | tail -n1)}
[[ -n $snapshot ]] || die "$PA_VM has no pre-update snapshot"

if [[ $PA_VM_ROLE == librarian ]] && vm_running; then
  die "$PA_VM is running: is a librarian run going? Roll it back once it's done"
fi
log "restoring $PA_VM to $snapshot"
incus stop "$PA_VM" 2>/dev/null || true
incus snapshot restore "$PA_VM" "$snapshot"
if [[ $PA_VM_ROLE == librarian ]]; then
  log "$PA_VM is back at $snapshot, and stopped until a run starts it"
  exit 0
fi
incus start "$PA_VM"
wait_for_agent
# A snapshot from before the store was mounted has no store devices.
[[ -d $PA_L0 ]] && "$DEPLOY_DIR/mount-store.sh"
log "$PA_VM is back at $snapshot"
