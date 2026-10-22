#!/usr/bin/env bash
# Usage: librarian-sandbox.sh start <l0> <project> <wiki>
#        librarian-sandbox.sh stop
#        librarian-sandbox.sh claude <arg>...
#        librarian-sandbox.sh kill
#
# The librarian's VM as the sandbox its sessions run in: what PA_LIBRARIAN_SANDBOX names for
# `pa librarian run` and `pa librarian replay` (pa_home.librarian; librarian.sh sets it).
#
# start shares the run's L0 and rendered librarian project into the VM read-only and its wiki
# writable, and nothing else, starts the VM, points api.anthropic.com at an address in the API's
# range, and prints where the sessions see the three, as JSON. It refuses a wiki that isn't on a
# filesystem of its own mounted nosuid,nodev,noexec (install-wiki.sh), since the VM writes it as
# it likes and the devbox commits it with git, and it refuses to start a VM that's running
# already: one run at a time.
#
# claude runs Claude Code in the VM as the librarian, in the project, with the setup token from
# PA_LIBRARIAN_TOKEN passed through stdin, ahead of the prompt, into CLAUDE_CODE_OAUTH_TOKEN: it
# is never on the VM's disk or a command line.
#
# kill kills every process of the librarian's user in the VM and waits until none is left: pa
# calls it when a session runs out of time, since killing `incus exec` on the devbox doesn't stop
# what it started in the VM, and the session must be dead before the run discards its writes.
#
# stop deletes the sessions' state (their transcripts hold private data, and a snapshot would
# keep them), stops the VM, and takes the shares off again. Safe to run at any time.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
select_vm librarian

usage() {
  die "usage: librarian-sandbox.sh start <l0> <project> <wiki> | stop | claude <arg>... | kill"
}

# share DEVICE SOURCE PATH READONLY attaches SOURCE at PATH in the VM as a disk device.
share() {
  incus config device add "$PA_VM" "$1" disk source="$2" path="$3" readonly="$4" >/dev/null
  log "$1: $2 at $3 (readonly=$4)"
}

unshare_all() {
  local device
  for device in "${PA_LIB_DEVICES[@]}"; do
    if incus config device get "$PA_VM" "$device" source >/dev/null 2>&1; then
      incus config device remove "$PA_VM" "$device" >/dev/null
    fi
  done
}

# inside DIR PARENT: DIR is PARENT or under it.
inside() { [[ $1/ == "$2"/* ]]; }

start() {
  (($# == 3)) || usage
  local l0 project wiki git_dir missing address dir path
  for dir in "$@"; do [[ -d $dir ]] || die "$dir isn't a directory"; done
  l0=$(realpath "$1") project=$(realpath "$2") wiki=$(realpath "$3")
  # The wiki's git directory is beside it (pa_home.config.DataRoot, ReplayTarget).
  git_dir=$(dirname "$wiki")/wiki.git

  # No directory the VM can write may be its sessions' working directory or above it, and
  # nothing git reads may be visible to it.
  inside "$project" "$wiki" && die "the project ($project) is inside the wiki ($wiki)"
  inside "$wiki" "$project" && die "the wiki ($wiki) is inside the project ($project)"
  for dir in "$l0" "$project" "$wiki"; do
    inside "$git_dir" "$dir" && die "the wiki's git directory ($git_dir) is inside $dir"
  done
  [[ ! -e $wiki/.git ]] || die "$wiki holds a .git"
  missing=$(filesystem_missing_flags "$wiki") || die "can't tell what $wiki is mounted with"
  [[ -z $missing ]] || die "$wiki isn't on a filesystem mounted nosuid,nodev,noexec (it lacks" \
    "${missing//$'\n'/, }): the live wiki must be installed with install-wiki.sh, and a replay" \
    "must run into a directory under $PA_REPLAYS"

  incus info "$PA_VM" >/dev/null 2>&1 || die "$PA_VM doesn't exist: run create-vm.sh --vm librarian"
  vm_running && die "$PA_VM is running already: is another librarian run going? If not," \
    "take it down with librarian-sandbox.sh stop"
  address=$(api_address) || die "$PA_API_HOST doesn't resolve to an address in $PA_API_RANGE"

  unshare_all
  share lib-l0 "$l0" "$PA_LIB_VM_L0" true
  share lib-project "$project" "$PA_LIB_VM_PROJECT" true
  share lib-wiki "$wiki" "$PA_LIB_VM_WIKI" false
  log "starting $PA_VM"
  incus start "$PA_VM" >&2
  wait_for_agent
  # The VM's agent mounts each share at boot.
  for path in "$PA_LIB_VM_L0" "$PA_LIB_VM_PROJECT" "$PA_LIB_VM_WIKI"; do
    for _ in $(seq 15); do
      incus exec "$PA_VM" -- mountpoint -q "$path" && continue 2
      sleep 1
    done
    die "$path isn't mounted in $PA_VM"
  done
  # The bridge has no DNS, so the API's name comes from /etc/hosts.
  # shellcheck disable=SC2016 # $1 and $2 are expanded by the shell in the VM
  incus exec "$PA_VM" -- sh -c '
    { grep -v "[[:space:]]$2\$" /etc/hosts; echo "$1 $2"; } >/etc/hosts.new &&
      mv /etc/hosts.new /etc/hosts' sh "$address" "$PA_API_HOST"
  log "$PA_VM is up, with $PA_API_HOST at $address"

  printf '{"l0": "%s", "project": "%s", "wiki": "%s"}\n' \
    "$PA_LIB_VM_L0" "$PA_LIB_VM_PROJECT" "$PA_LIB_VM_WIKI"
}

stop() {
  (($# == 0)) || usage
  incus info "$PA_VM" >/dev/null 2>&1 || return 0
  if vm_running; then
    # The token never reaches ~/.claude; everything else in it is the run's.
    incus exec "$PA_VM" -- rm -rf "/home/$PA_USER/.claude" "/home/$PA_USER/.claude.json" || true
    log "stopping $PA_VM"
    incus stop "$PA_VM" --timeout 60 >&2 || incus stop "$PA_VM" --force >&2
  fi
  unshare_all
}

claude() {
  [[ -f $PA_LIBRARIAN_TOKEN ]] ||
    die "the librarian's setup token isn't at $PA_LIBRARIAN_TOKEN (the setup wizard saves it)"
  [[ $(stat -c %a "$PA_LIBRARIAN_TOKEN") == 600 ]] ||
    die "$PA_LIBRARIAN_TOKEN must be mode 0600, readable by you alone"
  vm_running || die "$PA_VM isn't running: librarian-sandbox.sh start comes first"
  local uid gid
  # incus exec forwards stdin: these must not take the prompt.
  uid=$(incus exec "$PA_VM" -- id -u "$PA_USER" </dev/null)
  gid=$(incus exec "$PA_VM" -- id -g "$PA_USER" </dev/null)
  # The token is the first line of stdin, and the prompt the rest.
  # shellcheck disable=SC2016 # expanded by the shell in the VM
  { tr -d '[:space:]' <"$PA_LIBRARIAN_TOKEN"; echo; cat; } |
    incus exec -T "$PA_VM" --user "$uid" --group "$gid" --cwd "$PA_LIB_VM_PROJECT" \
      --env HOME="/home/$PA_USER" --env USER="$PA_USER" -- \
      sh -c 'IFS= read -r CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN &&
        exec "$0" "$@"' "$PA_LIB_VM_CLAUDE" "$@"
}

kill_sessions() {
  (($# == 0)) || usage
  vm_running || return 0
  for _ in $(seq 30); do
    # pkill and pgrep exit 1 when no process matches.
    incus exec "$PA_VM" -- pkill -KILL -u "$PA_USER" </dev/null || true
    incus exec "$PA_VM" -- pgrep -u "$PA_USER" >/dev/null </dev/null || {
      log "killed the sessions in $PA_VM"
      return 0
    }
    sleep 1
  done
  die "$PA_USER's processes in $PA_VM won't die"
}

command=${1:-}
(($#)) && shift
case $command in
  start) start "$@" ;;
  stop) stop "$@" ;;
  claude) claude "$@" ;;
  kill) kill_sessions "$@" ;;
  *) usage ;;
esac
