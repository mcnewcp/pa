#!/usr/bin/env bash
# Installs the assistant VM's fences inside it (#81, #92), as root, so the assistant can't change
# them:
#
# - the sandbox's packages (bubblewrap, socat, apparmor), installed while the VM can still reach
#   its package mirror when they're missing, and the pa-bwrap AppArmor profile, which lets
#   bubblewrap make its user namespace in place of Ubuntu's bwrap-userns-restrict;
# - the managed settings, from managed-settings.json, at /etc/claude-code/, root's and mode 0644;
# - the assistant's layout (assistant-layout.sh): scratch and the evals' directory, its user
#   settings held empty by root, and no auto memory left.
#
# update.sh runs it on every update, after snapshotting the VM. Safe to re-run. check-isolation.sh
# checks the result, and runs a command in the sandbox.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# shellcheck disable=SC2016 # expanded by the shell in the VM
if ! incus exec "$PA_VM" -- sh -c \
  'for p; do dpkg-query -W -f "\${db:Status-Status}" "$p" 2>/dev/null | grep -qx installed || exit 1; done' \
  sh "${PA_VM_SANDBOX_PACKAGES[@]}"; then
  log "installing ${PA_VM_SANDBOX_PACKAGES[*]} in $PA_VM"
  incus exec "$PA_VM" -- apt-get update -q >/dev/null
  incus exec "$PA_VM" --env DEBIAN_FRONTEND=noninteractive -- \
    apt-get install -y -q --no-install-recommends "${PA_VM_SANDBOX_PACKAGES[@]}"
fi

incus file push --quiet --uid 0 --gid 0 --mode 0644 "$PA_BWRAP_PROFILE" \
  "$PA_VM$PA_VM_BWRAP_PROFILE"
# Ubuntu's profile is disabled (it stays so across reboots) and unloaded, and ours loaded.
# shellcheck disable=SC2016 # expanded by the shell in the VM
incus exec "$PA_VM" -- sh -c '
  set -eu
  ubuntu=$1 ours=$2
  if [ -e "$ubuntu" ]; then
    mkdir -p /etc/apparmor.d/disable
    ln -sf "$ubuntu" /etc/apparmor.d/disable/
    apparmor_parser -R "$ubuntu" 2>/dev/null || true
  fi
  apparmor_parser -r "$ours"
' sh "$PA_VM_UBUNTU_BWRAP_PROFILE" "$PA_VM_BWRAP_PROFILE"
log "the sandbox's AppArmor profile is loaded in $PA_VM"

incus exec "$PA_VM" -- install -d -m 0755 "$(dirname "$PA_VM_MANAGED_SETTINGS")"
incus file push --quiet --uid 0 --gid 0 --mode 0644 "$PA_ASSISTANT_MANAGED_SETTINGS" \
  "$PA_VM$PA_VM_MANAGED_SETTINGS"
log "installed the managed settings at $PA_VM_MANAGED_SETTINGS"

incus exec "$PA_VM" -- bash -s -- "$PA_USER" "$PA_VM_HOME" "$PA_VM_SCRATCH" "$PA_VM_EVAL" \
  <"$DEPLOY_DIR/assistant-layout.sh"
log "laid out $PA_VM_SCRATCH, $PA_VM_EVAL and the assistant's user settings; no auto memory left"
