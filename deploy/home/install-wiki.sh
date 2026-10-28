#!/usr/bin/env bash
# Makes the directories the librarian's VM writes filesystems of their own
# (install-own-filesystem.sh): the wiki, and the replays directory a replay builds its wiki in
# (for eval wikis and rebuilds, `pa librarian replay $PA_REPLAYS/<name>`). The VM writes them as
# it likes, and the devbox commits them with git, so nothing written there may run, run setuid,
# or act as a device. librarian-sandbox.sh refuses a wiki anywhere else. The wiki's git
# directory stays beside it, outside its filesystem. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -d $PA_L0 ]] || die "$PA_DATA_ROOT has no L0: run create-data-root.sh first"
if ! mountpoint -q "$PA_WIKI" && [[ -n $(ls -A "$PA_WIKI" 2>/dev/null) ]]; then
  die "$PA_WIKI isn't empty: move it aside, re-run this, then move its files back in"
fi
"$DEPLOY_DIR/install-own-filesystem.sh" "$PA_WIKI" "$PA_WIKI_IMAGE" "$PA_WIKI_SIZE" \
  "The PA wiki" "$PA_VM/pa-wiki" "$PA_LIBRARIAN_VM/lib-wiki"
"$DEPLOY_DIR/install-own-filesystem.sh" "$PA_REPLAYS" "$PA_REPLAYS_IMAGE" "$PA_REPLAYS_SIZE" \
  "The PA librarian's replays" "$PA_LIBRARIAN_VM/lib-wiki"

# The assistant's VM shares the wiki read-only: share it again if it was taken off.
if incus info "$PA_VM" >/dev/null 2>&1; then
  "$DEPLOY_DIR/mount-store.sh"
fi
