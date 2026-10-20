#!/usr/bin/env bash
# Usage: install-own-filesystem.sh <dir> <image> <size> <description> [<vm>/<device>...]
#
# Makes <dir> on the devbox a filesystem of its own, for a directory a VM writes: a fixed-size
# ext4 image at <image>, loop-mounted nosuid,nodev,noexec. A share keeps the owners and modes
# the VM writes, so this is what stops root in the VM leaving a setuid program or a device node
# on the devbox, or filling its disk. A systemd mount unit mounts it at boot before Incus, which
# requires it: if it can't be mounted, no VM starts instead of sharing the bare directory.
#
# <dir> must be empty unless it's mounted already. Each <vm>/<device> sharing it is taken off
# first, since a share holds on to the directory it was given and would keep writing under the
# new mount; the caller shares it again. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($# >= 4)) || die "usage: install-own-filesystem.sh <dir> <image> <size> <description> [<vm>/<device>...]"
dir=$1 image=$2 size=$3 description=$4
shift 4
unit=$(systemd-escape -p --suffix=mount "$dir")

mounted=0
if mountpoint -q "$dir"; then
  mounted=1
else
  mkdir -p "$dir"
  [[ -z $(ls -A "$dir") ]] || die "$dir isn't empty"
  if [[ ! -f $image ]]; then
    log "creating $image ($size, ext4)"
    truncate -s "$size" "$image"
    chmod 0600 "$image"
    /usr/sbin/mkfs.ext4 -q -m 0 -E root_owner="$(id -u):$(id -g)" "$image"
    # Only what the VM writes belongs there, and lost+found would be root's.
    /usr/sbin/debugfs -w -R 'rmdir lost+found' "$image" 2>/dev/null
  fi
  for share in "$@"; do
    vm=${share%%/*} device=${share#*/}
    if incus config device get "$vm" "$device" source >/dev/null 2>&1; then
      incus config device remove "$vm" "$device" >/dev/null
      log "took $device off $vm until it's shared again"
    fi
  done
fi

flags=$(IFS=,; echo "${PA_OWN_FILESYSTEM_FLAGS[*]}")
unit_file=/etc/systemd/system/$unit
contents=$(cat <<UNIT
[Unit]
Description=$description, a filesystem of its own
# Before Incus, and required by it: if this can't be mounted, Incus doesn't start and no VM
# shares the bare directory.
Before=incus.service incus-startup.service

[Mount]
What=$image
Where=$dir
Type=ext4
Options=loop,$flags

[Install]
RequiredBy=incus.service
UNIT
)
changed=0
if [[ $(cat "$unit_file" 2>/dev/null) != "$contents" ]]; then
  printf '%s\n' "$contents" | sudo tee "$unit_file" >/dev/null
  changed=1
fi
sudo systemctl daemon-reload
sudo systemctl enable --now "$unit"
# A mount already in place keeps its old options until it is remounted; reloading a mount unit
# remounts it, without the unmount that would stop Incus with it.
if ((mounted && changed)); then
  log "remounting $dir with $flags"
  sudo systemctl reload "$unit"
fi

missing=$(missing_mount_flags "$dir") || die "$dir isn't mounted"
[[ -z $missing ]] || die "$dir is mounted without: ${missing//$'\n'/, }"
log "$dir is its own filesystem ($size, $flags), mounted at boot"
