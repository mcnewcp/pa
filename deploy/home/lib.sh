# Shared names and helpers for the deploy scripts. Source it; don't run it.
# The names are used by the scripts that source this; sudo incus means the binary.
# shellcheck shell=bash disable=SC2034,SC2032,SC2033

PA_VM=pa-home
PA_PROFILE=pa
PA_POOL=pa
PA_POOL_SIZE=120GiB
PA_BRIDGE=pabr0
PA_IMAGE=images:ubuntu/26.04/cloud
PA_REPO_URL=https://github.com/mcnewcp/pa.git
PA_USER=assistant
PA_REPO_DIR=/home/assistant/pa

# The instance's data root on the devbox: v0.11's, seeded from the synthetic corpus and
# disposable (v0.13 starts a fresh one for real sources). owner.env holds the instance owner.
PA_DATA_ROOT=$HOME/.local/share/pa-home-synthetic
PA_OWNER_ENV=$PA_DATA_ROOT/owner.env
# The two parts of it the VM sees.
PA_L0=$PA_DATA_ROOT/l0
PA_CHAT_INBOX=$PA_DATA_ROOT/inbox/assistant_chat
# The inbox directory is a filesystem of its own, loop-mounted from this image
# (install-chat-inbox.sh).
PA_CHAT_INBOX_IMAGE=$PA_DATA_ROOT/chat-inbox.img
PA_CHAT_INBOX_SIZE=1G
# What it's mounted with, so nothing the VM writes there runs, runs setuid, or acts as a device.
PA_CHAT_INBOX_FLAGS=(nosuid nodev noexec)

# Where the VM sees the store: L0 read-only, and its own inbox directory, its only way back.
PA_VM_L0=/srv/pa/l0
PA_VM_CHAT_INBOX=/srv/pa/inbox/assistant_chat
# The VM's copy of owner.env, and the agent project rendered from it.
PA_VM_OWNER_ENV=/home/assistant/.config/pa/owner.env
PA_VM_PROJECT=/home/assistant/agent
PA_VM_SCRATCH=/home/assistant/scratch

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd "$DEPLOY_DIR/../.." && pwd)

log() { printf '==> %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# incus runs through sudo until the shell picks up the incus-admin group (a new login).
incus() {
  if id -nG | tr ' ' '\n' | grep -qx incus-admin; then
    command incus "$@"
  else
    sudo incus "$@"
  fi
}

# as_assistant runs the bash script on stdin as the VM's assistant user, with a login shell.
# Arguments become the script's positional parameters.
as_assistant() {
  local args=""
  (($#)) && args=$(printf ' %q' "$@")
  incus exec "$PA_VM" -- runuser -l "$PA_USER" -c "bash -s --$args"
}

# require_owner_env stops unless the data root is seeded, owner.env with it.
require_owner_env() {
  [[ -f $PA_OWNER_ENV ]] || die "$PA_OWNER_ENV is missing: run seed-data-root.sh first"
}

# chat_inbox_missing_flags prints each of PA_CHAT_INBOX_FLAGS the inbox directory is mounted
# without, one per line. It fails if the inbox directory isn't a mount of its own.
chat_inbox_missing_flags() {
  local options flag
  options=$(findmnt -rn -M "$PA_CHAT_INBOX" -o OPTIONS) || return 1
  for flag in "${PA_CHAT_INBOX_FLAGS[@]}"; do
    [[ ,$options, == *,$flag,* ]] || echo "$flag"
  done
}

# wait_for_agent waits until the VM's Incus agent answers, after a start or a restore.
wait_for_agent() {
  for _ in $(seq 60); do
    incus exec "$PA_VM" -- true >/dev/null 2>&1 && return 0
    sleep 2
  done
  die "the $PA_VM agent didn't come up within 2 minutes"
}
