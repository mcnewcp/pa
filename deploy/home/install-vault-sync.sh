#!/usr/bin/env bash
# Installs Obsidian's headless Sync client on the devbox (#87), at its pinned version on a pinned
# Node 22, under PA_VAULT_SYNC_DIR, and the unit that runs it over the vault copy, pull-only:
#
#   install-vault-sync.sh            install or update; restart the unit if it's enabled
#   install-vault-sync.sh --enable   then enable and start it (the vault must be set up pull-only)
#
# Node comes from nodejs.org and is checked against PA_NODE_SHA256. The client and every package
# it needs come from npm's registry, each checked against vault-sync/package-lock.json.
# better-sqlite3's install script fetches its prebuilt binary from its GitHub releases. Safe to
# re-run: what's already at its pinned version is kept.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

enable=false
case ${1:-} in
  "") ;;
  --enable) enable=true ;;
  *) die "usage: install-vault-sync.sh [--enable]" ;;
esac
[[ $(uname -sm) == "Linux x86_64" ]] || die "the pinned Node is Linux x64's, and this is $(uname -sm)"

node_dir=$PA_VAULT_SYNC_DIR/node
node=$node_dir/bin/node
client_dir=$PA_VAULT_SYNC_DIR/node_modules/obsidian-headless
mkdir -p "$PA_VAULT_SYNC_DIR/bin"

# Node, checked against its pinned checksum before it's unpacked.
fresh_node=false
if [[ $("$node" --version 2>/dev/null || true) != "v$PA_NODE_VERSION" ]]; then
  tarball=node-v$PA_NODE_VERSION-linux-x64.tar.xz
  mkdir -p "$PA_NODE_CACHE"
  if [[ ! -f $PA_NODE_CACHE/$tarball ]]; then
    log "download Node $PA_NODE_VERSION"
    curl -fsSL -o "$PA_NODE_CACHE/$tarball.part" "$PA_NODE_DIST/v$PA_NODE_VERSION/$tarball"
    mv "$PA_NODE_CACHE/$tarball.part" "$PA_NODE_CACHE/$tarball"
  fi
  if ! sha256sum -c --quiet - <<<"$PA_NODE_SHA256  $PA_NODE_CACHE/$tarball"; then
    rm -f "$PA_NODE_CACHE/$tarball"
    die "Node $PA_NODE_VERSION's checksum doesn't match PA_NODE_SHA256: not installed"
  fi
  staging=$(mktemp -d "$PA_VAULT_SYNC_DIR/.node.XXXXXX")
  tar -xJf "$PA_NODE_CACHE/$tarball" -C "$staging" --strip-components=1
  rm -rf "$node_dir"
  mv "$staging" "$node_dir"
  fresh_node=true
fi
[[ $("$node" --version) == "v$PA_NODE_VERSION" ]] || die "Node in $node_dir isn't $PA_NODE_VERSION"
log "Node $PA_NODE_VERSION in $node_dir"

# The client and its packages, exactly as locked. A new Node needs them again: better-sqlite3 is
# built for one Node.
installed_version() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
  "$client_dir/package.json" 2>/dev/null || true; }
if $fresh_node || [[ $(installed_version) != "$PA_VAULT_SYNC_VERSION" ]] ||
  ! cmp -s "$DEPLOY_DIR/vault-sync/package-lock.json" "$PA_VAULT_SYNC_DIR/package-lock.json"; then
  log "install obsidian-headless $PA_VAULT_SYNC_VERSION"
  install -m 0644 "$DEPLOY_DIR/vault-sync/package.json" "$DEPLOY_DIR/vault-sync/package-lock.json" \
    "$PA_VAULT_SYNC_DIR/"
  (cd "$PA_VAULT_SYNC_DIR" &&
    PATH=$node_dir/bin:$PATH npm ci --omit=dev --no-audit --no-fund --loglevel=error)
fi
[[ $(installed_version) == "$PA_VAULT_SYNC_VERSION" ]] ||
  die "the client in $client_dir isn't $PA_VAULT_SYNC_VERSION"
[[ $("$node" "$client_dir/cli.js" --version) == "$PA_VAULT_SYNC_VERSION" ]] ||
  die "the client doesn't run on Node $PA_NODE_VERSION"
log "obsidian-headless $PA_VAULT_SYNC_VERSION in $PA_VAULT_SYNC_DIR"

# The runner the unit starts, and `ob` for the owner's steps (login, setup).
install -m 0755 "$DEPLOY_DIR/vault-sync/pa-vault-sync" "$PA_VAULT_SYNC_DIR/bin/pa-vault-sync"
printf '#!/bin/sh\nexec %q %q "$@"\n' "$node" "$client_dir/cli.js" >"$PA_VAULT_SYNC_DIR/bin/ob"
chmod 0755 "$PA_VAULT_SYNC_DIR/bin/ob"

# The copy and the client's state, the devbox user's alone. The unit can write nothing else.
mkdir -p "$PA_VAULT" "$PA_VAULT_SYNC_CONFIG"
chmod 0700 "$PA_VAULT" "$PA_VAULT_SYNC_CONFIG"

unit=/etc/systemd/system/$PA_VAULT_SYNC_UNIT
rendered=$(mktemp)
render_vault_sync_unit >"$rendered"
if ! sudo cmp -s "$rendered" "$unit"; then
  sudo install -m 0644 "$rendered" "$unit"
  sudo systemctl daemon-reload
  log "installed $unit"
fi
rm -f "$rendered"

if $enable; then
  "$PA_VAULT_SYNC_DIR/bin/pa-vault-sync" check "$PA_VAULT" >/dev/null ||
    die "set the vault up pull-only first (the wizard's sync stage)"
  sudo systemctl enable "$PA_VAULT_SYNC_UNIT"
fi
if systemctl is-enabled --quiet "$PA_VAULT_SYNC_UNIT"; then
  sudo systemctl restart "$PA_VAULT_SYNC_UNIT"
  log "$PA_VAULT_SYNC_UNIT (re)started"
else
  log "$PA_VAULT_SYNC_UNIT is installed but not enabled: install-vault-sync.sh --enable starts it"
fi
