#!/usr/bin/env bash
# Usage: update.sh <commit|branch|tag>
#        update.sh --vm librarian
#
# Snapshots a PA VM, then updates it. Roll back with rollback.sh (with the same --vm).
#
# The assistant's VM reaches nothing but the Anthropic API (#91), so the devbox pushes in all it
# needs, and nothing in the VM goes online:
#
# - the code: <ref>, resolved in this checkout (git fetch first), as a git bundle, with uv (the
#   devbox's own) and a wheelhouse (build_wheelhouse: a uv cache the devbox fills for that
#   commit's lock and the VM's Python). As root, install-code.sh checks the commit out in /opt/pa
#   and syncs its venv offline, so the code and the interpreter the hooks run with are root's and
#   read-only to the assistant (#92);
# - Claude Code at PA_ASSISTANT_CLAUDE_VERSION, downloaded and verified on the devbox
#   (claude_code_binary), at /usr/local/bin/claude, root's. Its own updates are off (the managed
#   settings), so it changes only when the pin is bumped here;
# - owner.env, as root's.
#
# Then it installs the fences (install-assistant-fences.sh: the managed settings, the sandbox, the
# assistant's layout, the OS's own updates off), pins the API's names (pin-hosts.sh), renders the
# agent project as root into a stable directory, reading L0 and the status where the VM mounts
# them, and the wiki too once the librarian has run into it (render-agent-project.sh), and
# installs the Remote Control unit, restarting it once the wizard has enabled it.
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
commit=$(git -C "$REPO_DIR" rev-parse --verify --quiet --end-of-options "$ref^{commit}") ||
  die "$ref isn't a commit in this checkout ($REPO_DIR): git fetch, and name it as git does"

# Everything that can fail on the devbox comes before the VM is touched.
claude=$(claude_code_binary "$PA_ASSISTANT_CLAUDE_VERSION")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
make_bundle "$REPO_DIR" "$commit" "$work/pa.bundle"
python=$(incus exec "$PA_VM" -- "$PA_VM_SYSTEM_PYTHON" -c \
  'import sys; print("%d.%d" % sys.version_info[:2])' </dev/null)
log "building the wheelhouse for $commit on Python $python"
mkdir -p "$work/wheelhouse"
build_wheelhouse "$REPO_DIR" "$commit" "$python" "$work/wheelhouse"

snapshot_vm pre-update

# Remote Control runs in the rendered project, which the render deletes, and its sessions' hooks
# run the repo's code. install-remote-control.sh starts it again. Stopping a unit that isn't
# installed yet fails, harmlessly.
rc_unit stop &>/dev/null || true
trap 'log "the update failed, leaving $PA_VM_RC_UNIT stopped: fix and re-run, or rollback.sh"' ERR

log "deploying $commit ($ref)"
incus exec "$PA_VM" -- install -d -m 0700 "$(dirname "$PA_VM_BUNDLE")" </dev/null
incus file push --quiet --uid 0 --gid 0 --mode 0755 "$(command -v uv)" "$PA_VM$PA_VM_UV"
incus file push --quiet --uid 0 --gid 0 --mode 0600 "$work/pa.bundle" "$PA_VM$PA_VM_BUNDLE"
# shellcheck disable=SC2016 # expanded by the shell in the VM
tar -C "$work/wheelhouse" -cf - . | incus exec "$PA_VM" -- sh -c \
  'rm -rf "$1" && mkdir -m 0700 "$1" && tar -C "$1" --no-same-owner -xf -' sh "$PA_VM_WHEELHOUSE"
incus exec "$PA_VM" -- bash -s -- "$PA_REPO_DIR" "$PA_VM_BUNDLE" "$commit" "$PA_VM_UV" \
  "$PA_VM_WHEELHOUSE" "$PA_VM_SYSTEM_PYTHON" <"$DEPLOY_DIR/install-code.sh"

# Claude Code is root's, so the assistant can't replace it. The copy the assistant installed
# itself before #91 goes.
incus file push --quiet --uid 0 --gid 0 --mode 0755 "$claude" "$PA_VM$PA_VM_CLAUDE"
incus exec "$PA_VM" -- rm -rf "${PA_VM_OLD_CLAUDE[@]}" </dev/null
installed=$(incus exec "$PA_VM" -- runuser -u "$PA_USER" -- "$PA_VM_CLAUDE" --version </dev/null)
[[ $installed == "$PA_ASSISTANT_CLAUDE_VERSION "* ]] ||
  die "$PA_VM runs Claude Code $installed, not $PA_ASSISTANT_CLAUDE_VERSION"
log "$PA_VM runs Claude Code $installed"

# The instance's values reach the VM as its environment: the owner from owner.env, root's, and
# the paths the store is mounted at.
incus exec "$PA_VM" -- install -d -m 0755 "$(dirname "$PA_VM_OWNER_ENV")" </dev/null
incus file push --quiet --uid 0 --gid 0 --mode 0644 "$PA_OWNER_ENV" "$PA_VM$PA_VM_OWNER_ENV"

"$DEPLOY_DIR/install-assistant-fences.sh"
"$DEPLOY_DIR/pin-hosts.sh"

log "rendering the agent project into $PA_VM_PROJECT"
incus exec "$PA_VM" -- bash -s -- "$PA_REPO_DIR" "$PA_VM_OWNER_ENV" "$PA_VM_PROJECT" \
  "$PA_VM_L0" "$PA_VM_SCRATCH" "$PA_VM_WIKI" "$PA_VM_STATUS_FILE" \
  <"$DEPLOY_DIR/render-agent-project.sh"

"$DEPLOY_DIR/install-remote-control.sh"
