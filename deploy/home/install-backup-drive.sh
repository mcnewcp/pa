#!/usr/bin/env bash
# Usage: install-backup-drive.sh <filesystem UUID>
#
# Mounts the USB SSD the nightly backup goes to at PA_BACKUP_MOUNT, found by its filesystem's
# UUID (lsblk -o UUID shows it), so no other drive is ever mounted there, whichever device name
# it gets. A systemd mount unit mounts it at boot, wanted but not required: without the drive
# the devbox still boots, the mount point stays empty, and `pa backup` records a missing
# repository in the status. Then it makes the repository's directory on the drive the owner's.
# The drive must be formatted already (ext4). Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($# == 1)) || die "usage: install-backup-drive.sh <filesystem UUID>"
uuid=$1
contents=$(backup_mount_unit "$uuid")
unit=$(backup_mount_unit_name)
lsblk -rno UUID | grep -qxF "$uuid" \
  || die "no attached filesystem has the UUID $uuid: plug in the backup drive, then check lsblk -o NAME,UUID"

unit_file=/etc/systemd/system/$unit
if [[ $(cat "$unit_file" 2>/dev/null) != "$contents" ]]; then
  printf '%s\n' "$contents" | sudo tee "$unit_file" >/dev/null
fi
sudo systemctl daemon-reload
sudo systemctl enable --now "$unit"

# Never on the root filesystem: a repository there would look like a backup that worked.
mountpoint -q "$PA_BACKUP_MOUNT" || die "the backup drive isn't mounted at $PA_BACKUP_MOUNT: see systemctl status '$unit'"
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0700 "$PA_BACKUP_REPOSITORY"
log "the backup drive is mounted at $PA_BACKUP_MOUNT, by its UUID, at every boot"
