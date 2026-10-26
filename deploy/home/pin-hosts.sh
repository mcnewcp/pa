#!/usr/bin/env bash
# Usage: pin-hosts.sh
#
# Pins the names the assistant's sessions and Remote Control need (PA_VM_PINNED_HOSTS: the API,
# and the host of the login's token refreshes) into the assistant VM's /etc/hosts, since its
# bridge has no DNS. Each name is resolved on the devbox, and only an address in the API's
# published range is pinned: if any name has none, the VM is left as it was. Old pins for those
# names are replaced, and every other line is kept.
#
# It runs at each start of the VM: at the devbox's boot (pa-home-pins.service, which fence-vm.sh
# installs), and from fence-vm.sh, update.sh and rollback.sh. The pins stay in the VM's /etc/hosts
# in between, since cloud-init leaves the file alone (profile.yaml). Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

pins=()
for host in "${PA_VM_PINNED_HOSTS[@]}"; do
  address=$(pinned_address "$host") ||
    die "$host doesn't resolve to an address in $PA_API_RANGE: has the API's range moved" \
      "(https://platform.claude.com/docs/en/api/ip-addresses)?"
  pins+=("$address $host")
done

wait_for_agent
hosts=$(mktemp)
trap 'rm -f "$hosts"' EXIT
incus exec "$PA_VM" -- cat /etc/hosts </dev/null | hosts_with_pins "${pins[@]}" >"$hosts"
incus file push --quiet --uid 0 --gid 0 --mode 0644 "$hosts" "$PA_VM/etc/hosts"
log "pinned in $PA_VM: $(IFS=,; echo "${pins[*]}")"
