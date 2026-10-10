#!/usr/bin/env bash
# Installs the PA VM's bridge firewall on the devbox as a systemd unit, so it loads at every
# boot before Incus starts the VM. Safe to re-run; reloads the rules.
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
fi
