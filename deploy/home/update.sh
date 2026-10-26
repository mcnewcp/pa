#!/usr/bin/env bash
# Usage: update.sh <commit|branch|tag>
#        update.sh --vm librarian
#
# Snapshots a PA VM, then updates it. Roll back with rollback.sh (with the same --vm).
#
# The assistant's VM: puts the repo at <ref> and runs `uv sync`, then renders the agent project
# for the instance owner into a stable directory, reading L0 and the status where the VM mounts
# them, and the wiki too once the librarian has run into it (render-agent-project.sh), and
# installs the Remote Control unit, restarting it once the wizard has enabled it. The first run
# installs uv and Claude Code and clones the repo.
#
# The librarian's VM: installs Claude Code at PA_LIBRARIAN_CLAUDE_VERSION, downloaded and
# verified on the devbox (install-librarian-claude.sh), and the managed settings. It needs no
# ref: nothing of the repo runs in it, and each run brings its own rendered project.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
take_vm_option "$@"
set -- "${ARGS[@]}"

if [[ $PA_VM_ROLE == librarian ]]; then
  (($# == 0)) || die "usage: update.sh --vm librarian (it takes no ref)"
  vm_running && die "$PA_VM is running: is a librarian run going? Update it once it's done"
  snapshot_vm pre-update
  exec "$DEPLOY_DIR/install-librarian-claude.sh"
fi

ref=${1:?usage: update.sh <commit|branch|tag>}
require_owner_env

snapshot_vm pre-update

# Remote Control runs in the rendered project, which the render deletes, and its sessions' hooks
# run the repo's code. install-remote-control.sh starts it again. Stopping a unit that isn't
# installed yet fails, harmlessly.
rc_unit stop &>/dev/null || true
trap 'log "the update failed, leaving $PA_VM_RC_UNIT stopped: fix and re-run, or rollback.sh"' ERR

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
as_assistant "$PA_REPO_DIR" "$PA_VM_OWNER_ENV" "$PA_VM_PROJECT" "$PA_VM_L0" "$PA_VM_SCRATCH" \
  "$PA_VM_WIKI" "$PA_VM_STATUS_FILE" <"$DEPLOY_DIR/render-agent-project.sh"

"$DEPLOY_DIR/install-remote-control.sh"
