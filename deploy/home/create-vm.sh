#!/usr/bin/env bash
# Creates the PA VM from profile.yaml and waits for cloud-init to finish. Re-running updates
# the profile (limits apply live) and leaves an existing VM alone.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

incus profile show "$PA_PROFILE" >/dev/null 2>&1 || incus profile create "$PA_PROFILE"
incus profile edit "$PA_PROFILE" <"$DEPLOY_DIR/profile.yaml"

if incus info "$PA_VM" >/dev/null 2>&1; then
  log "$PA_VM exists"
  exit 0
fi

log "launching $PA_VM from $PA_IMAGE (a VM, not a container)"
incus launch "$PA_IMAGE" "$PA_VM" --vm --profile "$PA_PROFILE"
wait_for_agent

log "waiting for cloud-init"
status=0
incus exec "$PA_VM" -- cloud-init status --wait >/dev/null || status=$?
case $status in
  0) ;;
  2) log "cloud-init finished with recoverable errors:"; incus exec "$PA_VM" -- cloud-init status --long ;;
  *) incus exec "$PA_VM" -- cloud-init status --long; die "cloud-init failed" ;;
esac

incus exec "$PA_VM" -- id "$PA_USER" >/dev/null || die "user $PA_USER is missing"
incus exec "$PA_VM" -- tailscale version >/dev/null || die "tailscale is missing"
log "$PA_VM is up"
