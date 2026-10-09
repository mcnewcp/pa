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
sudo systemctl restart pa-vm-firewall.service

sudo nft list table inet pa_vm >/dev/null || die "table inet pa_vm isn't loaded"
log "firewall loaded and enabled at boot"
