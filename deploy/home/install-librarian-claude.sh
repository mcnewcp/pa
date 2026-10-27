#!/usr/bin/env bash
# Installs Claude Code and its managed settings in the librarian's VM, which never reaches the
# download host. The devbox fetches the release PA_LIBRARIAN_CLAUDE_VERSION pins (its manifest,
# the manifest's signature and the linux-x64 binary) into PA_CLAUDE_CACHE, checks the signature
# against Anthropic's key by its fingerprint and the binary against the manifest's checksum,
# then pushes the binary to /usr/local/bin/claude and the managed settings to
# /etc/claude-code/, both owned by root, so the librarian can't replace either. The VM is started
# for this and stopped after. update.sh --vm librarian snapshots the VM first, then runs this.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
select_vm librarian

version=$PA_LIBRARIAN_CLAUDE_VERSION
binary=$(claude_code_binary "$version")

vm_running || { incus start "$PA_VM"; started=1; }
trap '[[ ${started:-} ]] && incus stop "$PA_VM"' EXIT
wait_for_agent

incus file push --quiet --uid 0 --gid 0 --mode 0755 "$binary" "$PA_VM$PA_LIB_VM_CLAUDE"
incus exec "$PA_VM" -- mkdir -p "$(dirname "$PA_LIB_VM_MANAGED_SETTINGS")"
incus file push --quiet --uid 0 --gid 0 --mode 0644 "$PA_LIBRARIAN_MANAGED_SETTINGS" \
  "$PA_VM$PA_LIB_VM_MANAGED_SETTINGS"

installed=$(incus exec "$PA_VM" -- runuser -u "$PA_USER" -- "$PA_LIB_VM_CLAUDE" --version)
[[ $installed == "$version "* ]] || die "$PA_VM runs Claude Code $installed, not $version"
log "$PA_VM runs Claude Code $installed, with the managed settings"
