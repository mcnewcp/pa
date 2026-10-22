#!/usr/bin/env bash
# Usage: create-vm.sh [--vm assistant|librarian]
#
# Creates a PA VM, by default the assistant's, from its profile (profile.yaml, or the
# librarian's in librarian/) and waits for cloud-init to finish. Re-running updates the profile
# (limits apply live) and leaves an existing VM alone. The librarian's VM is left stopped: it
# runs only for librarian runs (librarian-sandbox.sh).
set -euo pipefail
source "$(dirname "$0")/lib.sh"
take_vm_option "$@"
((${#ARGS[@]} == 0)) || die "usage: create-vm.sh [--vm assistant|librarian]"

incus profile show "$PA_PROFILE" >/dev/null 2>&1 || incus profile create "$PA_PROFILE" </dev/null
incus profile edit "$PA_PROFILE" <"$PA_VM_PROFILE_FILE"

if incus info "$PA_VM" >/dev/null 2>&1; then
  log "$PA_VM exists"
  exit 0
fi

log "launching $PA_VM from $PA_IMAGE (a VM, not a container)"
incus launch "$PA_IMAGE" "$PA_VM" --vm --profile "$PA_PROFILE" </dev/null
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
case $PA_VM_ROLE in
  assistant)
    incus exec "$PA_VM" -- tailscale version >/dev/null || die "tailscale is missing"
    log "$PA_VM is up"
    ;;
  librarian)
    # The wiki's share keeps owners as they are, with no mapping: the librarian can write the
    # wiki only because its uid is the devbox user's.
    vm_uid=$(incus exec "$PA_VM" -- id -u "$PA_USER")
    [[ $vm_uid == "$(id -u)" ]] ||
      die "$PA_USER is uid $vm_uid in $PA_VM but you are uid $(id -u): it couldn't write the wiki"
    incus stop "$PA_VM"
    log "$PA_VM is created, and stopped until a run starts it"
    ;;
esac
