#!/usr/bin/env bash
# Makes the VM's inbox directory on the devbox a filesystem of its own (install-own-filesystem.sh):
# a fixed-size ext4 image, loop-mounted nosuid,nodev,noexec at boot before Incus, so nothing the
# VM writes there runs, runs setuid, or acts as a device, and it can't fill the devbox's disk.
# Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -d $PA_L0 ]] || die "$PA_DATA_ROOT has no L0: run create-data-root.sh first"
if ! mountpoint -q "$PA_CHAT_INBOX" && [[ -n $(ls -A "$PA_CHAT_INBOX" 2>/dev/null) ]]; then
  die "$PA_CHAT_INBOX isn't empty: run ingest.sh, then re-run this"
fi
"$DEPLOY_DIR/install-own-filesystem.sh" "$PA_CHAT_INBOX" "$PA_CHAT_INBOX_IMAGE" \
  "$PA_CHAT_INBOX_SIZE" "The PA VM's inbox directory" "$PA_VM/pa-chat-inbox"

# Shares it again if it was taken off; otherwise changes nothing.
"$DEPLOY_DIR/mount-store.sh"
