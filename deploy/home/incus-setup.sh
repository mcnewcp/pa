#!/usr/bin/env bash
# Usage: incus-setup.sh [--vm assistant|librarian]
#
# Sets up Incus for a PA VM, by default the assistant's: a btrfs storage pool both VMs share, so
# snapshots are copy-on-write and cheap, and a bridge of the VM's own with IPv6 off, so the host
# firewall fences IPv4 only. The bridge has no DNS, since a resolver that forwards lookups is a way
# out its ACL can't close, and the VM's NIC gets an egress ACL (PA_ACL, from PA_ACL_FILE) letting
# out only TCP 443 to the API's range (#91).
#
# For the assistant, it also makes the open bridge its VM is built on (PA_BUILD_BRIDGE): DNS and
# NAT, IPv6 off, and the host firewall dropping everything of the owner's (create-vm.sh).
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

# ensure_bridge NAME: a NAT'd IPv4 bridge named NAME, with IPv6 off.
ensure_bridge() {
  if incus network show "$1" >/dev/null 2>&1; then
    log "bridge $1 exists"
  else
    log "creating bridge $1"
    # With stdin not a terminal, create reads the new object's config from it, and waits forever
    # on one that never closes.
    incus network create "$1" ipv4.address=auto ipv4.nat=true ipv6.address=none </dev/null
  fi
  incus network set "$1" ipv6.address=none
}

ensure_bridge "$PA_BRIDGE"
# The bridge's dnsmasq serves DHCP only: nothing listens on port 53.
incus network set "$PA_BRIDGE" raw.dnsmasq=port=0
incus network acl show "$PA_ACL" >/dev/null 2>&1 || incus network acl create "$PA_ACL" </dev/null
# Edit it only when it differs: Incus 6.0 fails to apply an edit while the ACL's only instance is
# stopped, as the librarian's is between runs.
if incus query "/1.0/network-acls/$PA_ACL" | acl_differs "$PA_ACL_FILE"; then
  incus network acl edit "$PA_ACL" <"$PA_ACL_FILE"
fi
log "bridge $PA_BRIDGE has no DNS, and ACL $PA_ACL lets out only TCP 443 to $PA_API_RANGE"

if [[ $PA_VM_ROLE == assistant ]]; then
  ensure_bridge "$PA_BUILD_BRIDGE"
  log "bridge $PA_BUILD_BRIDGE, open, for building the VM: $(incus network get "$PA_BUILD_BRIDGE" ipv4.address)"
fi

log "bridge $PA_BRIDGE: $(incus network get "$PA_BRIDGE" ipv4.address)"
