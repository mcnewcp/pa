#!/usr/bin/env bash
# Usage: rollback.sh [snapshot]
#
# Restores the PA VM to a snapshot, by default the latest one update.sh took.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

snapshot=${1:-$(incus query "/1.0/instances/$PA_VM/snapshots" |
  grep -o 'snapshots/pre-update-[^"]*' | sed 's|^snapshots/||' | sort | tail -n1)}
[[ -n $snapshot ]] || die "$PA_VM has no pre-update snapshot"

log "restoring $PA_VM to $snapshot"
incus stop "$PA_VM" 2>/dev/null || true
incus snapshot restore "$PA_VM" "$snapshot"
incus start "$PA_VM"
wait_for_agent
log "$PA_VM is back at $snapshot"
