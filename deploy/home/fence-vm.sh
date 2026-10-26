#!/usr/bin/env bash
# Usage: fence-vm.sh
#
# Fences the assistant's VM (#91), so it reaches nothing but TCP 443 in the Anthropic API's
# range: no DNS, no Tailscale, nothing of the owner's. Run it after create-vm.sh, which builds
# the VM open, and before anything is shared into it or any credential entered (mount-store.sh
# refuses an unfenced VM). It's also how the live VM, built before #91, is fenced. Safe to re-run.
#
# Its bridge and the firewall must be fenced already (incus-setup.sh, install-firewall.sh). Then:
#
# 1. While the VM can still reach the internet: a snapshot (pre-fence-<time>); the fences inside
#    it (install-assistant-fences.sh), which install the sandbox's packages if they're missing
#    and mask the OS's own updates; and Tailscale logged out and purged, if it's there.
# 2. The API's names pinned into its /etc/hosts (pin-hosts.sh), so Remote Control finds the API
#    the moment the VM is back.
# 3. The VM stopped, its profile's fenced NIC (profile.yaml) put in place of the build bridge's,
#    and started again; the pins checked again.
# 4. pa-pins@<vm>.service on the devbox, which pins the names again at every boot.
# 5. A quick look from inside: no name resolves, and the API answers. check-isolation.sh checks
#    the rest.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
select_vm assistant
(($# == 0)) || die "usage: fence-vm.sh"

incus info "$PA_VM" >/dev/null 2>&1 || die "$PA_VM doesn't exist: run create-vm.sh first"
[[ $(incus network get "$PA_BRIDGE" raw.dnsmasq </dev/null) == *port=0* ]] ||
  die "$PA_BRIDGE serves DNS: run incus-setup.sh first"
if incus query "/1.0/network-acls/$PA_ACL" </dev/null | acl_differs "$PA_ACL_FILE"; then
  die "the ACL $PA_ACL isn't the repo's ($PA_ACL_FILE): run incus-setup.sh first"
fi
sudo nft list chain inet pa_vm fenced_out >/dev/null ||
  die "the bridge firewall has no fenced_out chain: run install-firewall.sh first"

vm_running || incus start "$PA_VM" </dev/null
wait_for_agent
snapshot_vm pre-fence

"$DEPLOY_DIR/install-assistant-fences.sh"
if incus exec "$PA_VM" -- sh -c 'command -v tailscale' >/dev/null 2>&1 </dev/null; then
  log "logging $PA_VM out of the tailnet, and removing Tailscale"
  incus exec "$PA_VM" -- timeout 30 tailscale logout </dev/null || log "the logout failed: remove $PA_VM in the Tailscale admin console"
  incus exec "$PA_VM" --env DEBIAN_FRONTEND=noninteractive -- apt-get purge -y -q tailscale </dev/null
  incus exec "$PA_VM" -- rm -rf /var/lib/tailscale /etc/apt/sources.list.d/tailscale.list \
    /usr/share/keyrings/tailscale-archive-keyring.gpg </dev/null
fi
"$DEPLOY_DIR/pin-hosts.sh"

log "moving $PA_VM behind its fence"
incus profile edit "$PA_PROFILE" <"$PA_VM_PROFILE_FILE"
incus stop "$PA_VM" </dev/null
# create-vm.sh's NIC on the build bridge shadows the profile's until it's removed.
if incus query "/1.0/instances/$PA_VM" </dev/null |
  python3 -c 'import json, sys; sys.exit(0 if "eth0" in (json.load(sys.stdin).get("devices") or {}) else 1)'; then
  incus config device remove "$PA_VM" eth0 >/dev/null
fi
incus start "$PA_VM" </dev/null
wait_for_agent
"$DEPLOY_DIR/pin-hosts.sh"
require_fenced

render_pins_unit | sudo tee /etc/systemd/system/pa-pins@.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable "pa-pins@$PA_VM.service"
log "pa-pins@$PA_VM.service pins the API's names into $PA_VM at every boot of the devbox"

if incus exec "$PA_VM" -- getent hosts example.com >/dev/null 2>&1 </dev/null; then
  die "$PA_VM still resolves example.com: its bridge serves DNS"
fi
incus exec "$PA_VM" -- curl -sS -o /dev/null -m 10 "https://$PA_API_HOST" </dev/null ||
  die "$PA_VM can't reach https://$PA_API_HOST"
log "$PA_VM is fenced: no DNS, and the API answers. Run check-isolation.sh for every fence"
