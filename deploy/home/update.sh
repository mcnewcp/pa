#!/usr/bin/env bash
# Usage: update.sh <commit|branch|tag>
#        update.sh --vm librarian
#
# Snapshots a PA VM, then updates it. Roll back with rollback.sh (with the same --vm).
#
# The assistant's VM: as root, puts the repo at <ref> in /opt/pa and runs `uv sync`, so the code
# and the interpreter the hooks run with are root's and read-only to the assistant; puts owner.env
# in as root's; installs the fences (install-assistant-fences.sh: the managed settings, the
# sandbox, the assistant's layout); renders the agent project as root into a stable directory,
# reading L0 where the VM mounts it, and the wiki too once the librarian has run into it
# (render-agent-project.sh); and installs the Remote Control unit, restarting it once the wizard
# has enabled it. The first run installs uv (root's) and Claude Code (the assistant's) and clones
# the repo.
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
incus exec "$PA_VM" -- bash -s -- "$PA_REPO_URL" "$PA_REPO_DIR" "$ref" "$PA_VM_UV" \
  "$PA_VM_PYTHON_DIR" <<'EOF'
set -euo pipefail
repo_url=$1 repo_dir=$2 ref=$3 uv=$4 python_dir=$5
umask 022

[[ -x $uv ]] || curl -fsSL https://astral.sh/uv/install.sh |
  env UV_INSTALL_DIR="${uv%/*}" UV_NO_MODIFY_PATH=1 sh >/dev/null

[[ -d $repo_dir/.git ]] || git clone --quiet "$repo_url" "$repo_dir"
cd "$repo_dir"
git fetch --quiet origin "$ref"
git checkout --quiet --detach FETCH_HEAD
# The assistant reads the commit (an eval run records it) from a checkout that isn't its own.
git config --system --replace-all safe.directory "$repo_dir" "^$repo_dir\$"
# Any interpreter uv installs goes where the assistant can run it and not change it.
UV_PYTHON_INSTALL_DIR=$python_dir "$uv" sync --locked --compile-bytecode
chmod -R go-w,a+rX "$repo_dir"
[[ ! -d $python_dir ]] || chmod -R go-w,a+rX "$python_dir"

echo "deployed $(git rev-parse HEAD)"
EOF

# Claude Code is the assistant's for now; its managed settings turn its updates off.
as_assistant <<'EOF'
set -euo pipefail
[[ -x $HOME/.local/bin/claude ]] || curl -fsSL https://claude.ai/install.sh | bash
EOF

# The instance's values reach the VM as its environment: the owner from owner.env, root's, and
# the paths the store is mounted at.
incus exec "$PA_VM" -- install -d -m 0755 "$(dirname "$PA_VM_OWNER_ENV")"
incus file push --quiet --uid 0 --gid 0 --mode 0644 "$PA_OWNER_ENV" "$PA_VM$PA_VM_OWNER_ENV"

"$DEPLOY_DIR/install-assistant-fences.sh"

log "rendering the agent project into $PA_VM_PROJECT"
incus exec "$PA_VM" -- bash -s -- "$PA_REPO_DIR" "$PA_VM_OWNER_ENV" "$PA_VM_PROJECT" \
  "$PA_VM_L0" "$PA_VM_SCRATCH" "$PA_VM_WIKI" <"$DEPLOY_DIR/render-agent-project.sh"

"$DEPLOY_DIR/install-remote-control.sh"
