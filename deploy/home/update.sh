#!/usr/bin/env bash
# Usage: update.sh <commit|branch|tag>
#
# Snapshots the PA VM, then puts the repo at <ref> and runs `uv sync`. The first run installs
# uv and Claude Code and clones the repo. Roll back with rollback.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ref=${1:?usage: update.sh <commit|branch|tag>}

snapshot="pre-update-$(date -u +%Y%m%dT%H%M%SZ)"
log "snapshot $PA_VM/$snapshot"
incus snapshot create "$PA_VM" "$snapshot"

log "deploying $ref"
as_assistant "$PA_REPO_URL" "$PA_REPO_DIR" "$ref" <<'EOF'
set -euo pipefail
repo_url=$1 repo_dir=$2 ref=$3
bin=$HOME/.local/bin

[[ -x $bin/uv ]] || curl -fsSL https://astral.sh/uv/install.sh | sh
[[ -x $bin/claude ]] || curl -fsSL https://claude.ai/install.sh | bash

[[ -d $repo_dir/.git ]] || git clone --quiet "$repo_url" "$repo_dir"
cd "$repo_dir"
git fetch --quiet origin "$ref"
git checkout --quiet --detach FETCH_HEAD
"$bin/uv" sync --locked

echo "deployed $(git rev-parse HEAD)"
EOF
