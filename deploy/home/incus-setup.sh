#!/usr/bin/env bash
# Sets up Incus for the PA VM: a btrfs storage pool, so snapshots are copy-on-write and cheap,
# and a bridge of its own with IPv6 off, so the host firewall fences IPv4 only. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if incus storage show "$PA_POOL" >/dev/null 2>&1; then
  log "storage pool $PA_POOL exists"
else
  log "creating btrfs storage pool $PA_POOL ($PA_POOL_SIZE, loop-backed)"
  incus storage create "$PA_POOL" btrfs size="$PA_POOL_SIZE"
fi

if incus network show "$PA_BRIDGE" >/dev/null 2>&1; then
  log "bridge $PA_BRIDGE exists"
else
  log "creating bridge $PA_BRIDGE"
  incus network create "$PA_BRIDGE" ipv4.address=auto ipv4.nat=true ipv6.address=none
fi
incus network set "$PA_BRIDGE" ipv6.address=none

log "bridge $PA_BRIDGE: $(incus network get "$PA_BRIDGE" ipv4.address)"
