#!/usr/bin/env bash
# Usage: capture-user.sh <assistant> <capture> <capture uid> <assistant uid> <state dir>
#
# Runs in the assistant's VM, as root: install-assistant-fences.sh pipes it to `incus exec`. Makes
# the capture user (#94), which owns the chat inbox and is the only user the capture helper runs
# as, and the helper's state directory, the capture user's alone (mode 0700).
#
# The capture user has the devbox user's uid (<capture uid>): the inbox share keeps owners
# unmapped, so what it writes is the devbox user's on the devbox, where ingest reads and removes
# it, and the assistant, with a uid of its own, can't write the inbox. A VM rebuilt from
# profile.yaml has both users already. One built before #94 gave that uid to the assistant: it
# moves to <assistant uid> first, its group with it, along with what it owns on the VM's own disk
# (its home directory, scratch, the evals' directory). The shares are other filesystems and are
# left alone. Nothing may run as the assistant meanwhile. Safe to re-run.
set -euo pipefail
assistant=$1 capture=$2 capture_uid=$3 assistant_uid=$4 state=$5

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*" >&2; }

[[ $capture_uid != "$assistant_uid" ]] ||
  die "the capture user and $assistant can't share uid $capture_uid"
uid=$(id -u "$assistant") || die "there's no user $assistant"
if [[ $uid == "$capture_uid" ]]; then
  ! pgrep -u "$assistant" >/dev/null ||
    die "$assistant runs something (Remote Control, an eval, a login, its systemd --user):" \
      "stop it (loginctl terminate-user $assistant), then re-run"
  ! getent passwd "$assistant_uid" >/dev/null || die "uid $assistant_uid is taken"
  if [[ $(id -g "$assistant") == "$capture_uid" ]]; then
    ! getent group "$assistant_uid" >/dev/null || die "gid $assistant_uid is taken"
    groupmod -g "$assistant_uid" "$assistant"
    find / -xdev -gid "$capture_uid" -exec chgrp -h "$assistant_uid" {} +
  fi
  usermod -u "$assistant_uid" "$assistant"
  find / -xdev -uid "$capture_uid" -exec chown -h "$assistant_uid" {} +
  log "moved $assistant from uid $capture_uid to $assistant_uid"
fi

if ! id -u "$capture" >/dev/null 2>&1; then
  ! getent passwd "$capture_uid" >/dev/null || die "uid $capture_uid is taken"
  getent group "$capture" >/dev/null || groupadd -g "$capture_uid" "$capture"
  useradd -u "$capture_uid" -g "$capture" -M -d /nonexistent -s /usr/sbin/nologin "$capture"
  log "made the user $capture, uid $capture_uid"
fi
actual=$(id -u "$capture")
[[ $actual == "$capture_uid" ]] ||
  die "$capture is uid $actual, not $capture_uid (the devbox user's): rebuild the VM"

install -d -o "$capture" -g "$capture" -m 0700 "$state"
