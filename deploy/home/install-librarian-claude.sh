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
cache=$PA_CLAUDE_CACHE/$version
binary=$cache/claude-linux-x64

# verify: the cached binary is the one the signed manifest names.
verify() {
  local gnupghome status
  gnupghome=$(mktemp -d)
  status=0
  (
    export GNUPGHOME=$gnupghome
    gpg --quiet --batch --import "$cache/claude-code.asc" 2>/dev/null
    # The key must be the one the fingerprint pins, whatever the download host serves.
    gpg --batch --with-colons --fingerprint | grep -qx "fpr:::::::::$PA_CLAUDE_KEY_FINGERPRINT:" ||
      die "the key at $PA_CLAUDE_KEY_URL isn't $PA_CLAUDE_KEY_FINGERPRINT"
    gpg --batch --status-fd 1 --verify "$cache/manifest.json.sig" "$cache/manifest.json" 2>/dev/null |
      grep -q "^\[GNUPG:\] VALIDSIG $PA_CLAUDE_KEY_FINGERPRINT " ||
      die "the manifest for $version isn't signed by $PA_CLAUDE_KEY_FINGERPRINT"
  ) || status=$?
  rm -rf "$gnupghome"
  ((status == 0)) || exit "$status"
  python3 - "$cache/manifest.json" "$binary" "$version" <<'PY' || die "the binary doesn't match the signed manifest"
import hashlib, json, sys
manifest, binary, version = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
platform = manifest["platforms"]["linux-x64"]
digest = hashlib.sha256(open(binary, "rb").read()).hexdigest()
sys.exit(0 if manifest["version"] == version and digest == platform["checksum"] else 1)
PY
}

if [[ -f $binary ]] && verify; then
  log "Claude Code $version is downloaded and verified"
else
  log "downloading Claude Code $version"
  mkdir -p "$cache"
  for file in manifest.json manifest.json.sig; do
    curl -fsSL -o "$cache/$file" "$PA_CLAUDE_RELEASES/$version/$file"
  done
  curl -fsSL -o "$cache/claude-code.asc" "$PA_CLAUDE_KEY_URL"
  curl -fsSL -o "$binary.part" "$PA_CLAUDE_RELEASES/$version/linux-x64/claude"
  mv "$binary.part" "$binary"
  verify
  log "Claude Code $version: the manifest's signature and the binary's checksum check out"
fi

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
