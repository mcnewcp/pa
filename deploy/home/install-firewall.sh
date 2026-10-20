#!/usr/bin/env bash
# Installs the PA VMs' bridge firewall on the devbox as a systemd unit, so it loads at every
# boot before Incus starts a VM, and lets each bridge's traffic through ufw. Safe to re-run;
# reloads the rules.
#
# Don't enable nftables.service for this: Ubuntu's /etc/nftables.conf starts with
# `flush ruleset`, which would wipe Docker's and Incus's rules at boot.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

src=$DEPLOY_DIR/firewall
sudo nft -c -f "$src/pa-vm.nft"

sudo install -D -m 0644 "$src/pa-vm.nft" /etc/pa/pa-vm.nft
sudo install -D -m 0755 "$src/pa-vm-firewall" /usr/local/sbin/pa-vm-firewall
sudo install -D -m 0644 "$src/pa-vm-firewall.service" /etc/systemd/system/pa-vm-firewall.service
sudo systemctl daemon-reload
sudo systemctl enable pa-vm-firewall.service
# Start if it isn't running, then reload the rules directly: restarting the unit would restart
# Incus too, since Incus requires it.
sudo systemctl start pa-vm-firewall.service
sudo /usr/local/sbin/pa-vm-firewall

sudo nft list table inet pa_vm >/dev/null || die "table inet pa_vm isn't loaded"
log "firewall loaded and enabled at boot"

# ufw drops input and forwarded traffic by default, which would leave the VM with no DHCP, DNS
# or way out. Let those through ufw; pa_vm's drops still fence everything else. ufw saves its
# rules itself and skips ones it already has.
if sudo ufw status | grep -q '^Status: active'; then
  sudo ufw allow in on "$PA_BRIDGE" to any port 67 proto udp comment 'PA VM DHCP'
  sudo ufw allow in on "$PA_BRIDGE" to any port 53 comment 'PA VM DNS'
  sudo ufw route allow in on "$PA_BRIDGE" comment 'PA VM out'
  log "ufw lets the VM's DHCP, DNS and outbound traffic through"
  # The librarian's VM gets DHCP and its way to the API, and nothing more: its bridge has no DNS,
  # and the route rule names only the API's range, a fence of its own beside pa_vm and the ACL.
  sudo ufw allow in on "$PA_LIBRARIAN_BRIDGE" to any port 67 proto udp comment 'PA librarian DHCP'
  sudo ufw route allow in on "$PA_LIBRARIAN_BRIDGE" to "$PA_API_RANGE" port 443 proto tcp \
    comment 'PA librarian to the Anthropic API'
  log "ufw lets the librarian's DHCP and its traffic to $PA_API_RANGE:443 through"
fi
