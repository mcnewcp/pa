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

DEPLOY_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

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

# wait_for_agent waits until the VM's Incus agent answers, after a start or a restore.
wait_for_agent() {
  for _ in $(seq 60); do
    incus exec "$PA_VM" -- true >/dev/null 2>&1 && return 0
    sleep 2
  done
  die "the $PA_VM agent didn't come up within 2 minutes"
}
