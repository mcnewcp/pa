#!/usr/bin/env bash
# Makes the VM's inbox directory on the devbox a filesystem of its own: a fixed-size ext4 image,
# loop-mounted nosuid,nodev,noexec. The share keeps the owners and modes the VM writes, so this
# is what stops root in the VM leaving a setuid program or a device node on the devbox, or
# filling its disk. A systemd mount unit mounts it at boot before Incus, which requires it: if it
# can't be mounted, the VM stays down instead of sharing the bare directory. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -d $PA_L0 ]] || die "$PA_DATA_ROOT has no L0: run seed-data-root.sh first"
unit=$(systemd-escape -p --suffix=mount "$PA_CHAT_INBOX")

reshare=0
mounted=0
if mountpoint -q "$PA_CHAT_INBOX"; then
  mounted=1
else
  mkdir -p "$PA_CHAT_INBOX"
  [[ -z $(ls -A "$PA_CHAT_INBOX") ]] ||
    die "$PA_CHAT_INBOX isn't empty: run ingest.sh, then re-run this"
  if [[ ! -f $PA_CHAT_INBOX_IMAGE ]]; then
    log "creating $PA_CHAT_INBOX_IMAGE ($PA_CHAT_INBOX_SIZE, ext4)"
    truncate -s "$PA_CHAT_INBOX_SIZE" "$PA_CHAT_INBOX_IMAGE"
    chmod 0600 "$PA_CHAT_INBOX_IMAGE"
    /usr/sbin/mkfs.ext4 -q -m 0 -E root_owner="$(id -u):$(id -g)" "$PA_CHAT_INBOX_IMAGE"
    # Only payloads belong in the inbox, and lost+found would be root's.
    /usr/sbin/debugfs -w -R 'rmdir lost+found' "$PA_CHAT_INBOX_IMAGE" 2>/dev/null
  fi
  # The VM's share holds on to the directory it was given, so it would keep writing under the
  # new mount. Take it off now; it goes back on below.
  if incus config device get "$PA_VM" pa-chat-inbox source >/dev/null 2>&1; then
    incus config device remove "$PA_VM" pa-chat-inbox >/dev/null
    reshare=1
  fi
fi

flags=$(IFS=,; echo "${PA_CHAT_INBOX_FLAGS[*]}")
unit_file=/etc/systemd/system/$unit
contents=$(cat <<EOF
[Unit]
Description=The PA VM's inbox directory, a filesystem of its own
# Before Incus, and required by it: if this can't be mounted, Incus doesn't start and the VM
# stays down instead of sharing the bare directory.
Before=incus.service incus-startup.service

[Mount]
What=$PA_CHAT_INBOX_IMAGE
Where=$PA_CHAT_INBOX
Type=ext4
Options=loop,$flags

[Install]
RequiredBy=incus.service
EOF
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
  log "remounting $PA_CHAT_INBOX with $flags"
  sudo systemctl reload "$unit"
fi

missing=$(chat_inbox_missing_flags) || die "$PA_CHAT_INBOX isn't mounted"
[[ -z $missing ]] || die "$PA_CHAT_INBOX is mounted without: ${missing//$'\n'/, }"
log "$PA_CHAT_INBOX is its own filesystem ($PA_CHAT_INBOX_SIZE, $flags), mounted at boot"

if ((reshare)); then
  "$DEPLOY_DIR/mount-store.sh"
fi
