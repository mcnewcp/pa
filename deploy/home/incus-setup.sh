#!/usr/bin/env bash
# Usage: incus-setup.sh [--vm assistant|librarian]
#
# Sets up Incus for a PA VM, by default the assistant's: a btrfs storage pool both VMs share, so
# snapshots are copy-on-write and cheap, and a bridge of the VM's own with IPv6 off, so the host
# firewall fences IPv4 only. The librarian's bridge also has no DNS, since a resolver that
# forwards lookups is a way out its ACL can't close, and the librarian gets its egress ACL.
# Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
take_vm_option "$@"
((${#ARGS[@]} == 0)) || die "usage: incus-setup.sh [--vm assistant|librarian]"

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
  # With stdin not a terminal, create reads the new object's config from it, and waits forever
  # on one that never closes.
  incus network create "$PA_BRIDGE" ipv4.address=auto ipv4.nat=true ipv6.address=none </dev/null
fi
incus network set "$PA_BRIDGE" ipv6.address=none

if [[ $PA_VM_ROLE == librarian ]]; then
  # The bridge's dnsmasq serves DHCP only: nothing listens on port 53.
  incus network set "$PA_BRIDGE" raw.dnsmasq=port=0
  incus network acl show "$PA_LIBRARIAN_ACL" >/dev/null 2>&1 ||
    incus network acl create "$PA_LIBRARIAN_ACL" </dev/null
  # Edit it only when it differs: Incus 6.0 fails to apply an edit while the ACL's only
  # instance is stopped, as the librarian's is between runs.
  acl=$DEPLOY_DIR/librarian/egress-acl.json
  if incus query "/1.0/network-acls/$PA_LIBRARIAN_ACL" | acl_differs "$acl"; then
    incus network acl edit "$PA_LIBRARIAN_ACL" <"$acl"
  fi
  log "ACL $PA_LIBRARIAN_ACL lets out only TCP 443 to $PA_API_RANGE"
fi

log "bridge $PA_BRIDGE: $(incus network get "$PA_BRIDGE" ipv4.address)"
