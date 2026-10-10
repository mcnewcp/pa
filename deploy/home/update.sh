#!/usr/bin/env bash
# Usage: update.sh <commit|branch|tag>
#
# Snapshots the PA VM, puts the repo at <ref> and runs `uv sync`, then renders the agent project
# for the instance owner into a stable directory. The first run installs uv and Claude Code and
# clones the repo. Roll back with rollback.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ref=${1:?usage: update.sh <commit|branch|tag>}
require_owner_env

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

# The instance's values reach the VM as its environment: the owner from owner.env, and the paths
# the store is mounted at.
# shellcheck disable=SC2016 # $1 is expanded by the shell in the VM
incus exec "$PA_VM" -- runuser -u "$PA_USER" -- \
  sh -c 'umask 077 && mkdir -p "${1%/*}" && cat >"$1"' sh "$PA_VM_OWNER_ENV" <"$PA_OWNER_ENV"

log "rendering the agent project into $PA_VM_PROJECT"
as_assistant "$PA_REPO_DIR" "$PA_VM_OWNER_ENV" "$PA_VM_PROJECT" "$PA_VM_L0" "$PA_VM_SCRATCH" <<'EOF'
set -euo pipefail
repo_dir=$1 owner_env=$2 project=$3 l0=$4 scratch=$5
set -a
source "$owner_env"
set +a
# A rendered project is replaced, never written over. Its path stays the same, so Claude Code's
# folder trust and session history for it carry over.
rm -rf "$project"
mkdir -p "$scratch"
"$repo_dir/.venv/bin/pa" agent-project render "$project" --l0 "$l0" --scratch "$scratch"
EOF
