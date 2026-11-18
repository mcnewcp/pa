#!/usr/bin/env bash
# Usage: retire-data-root.sh <synthetic data root>
#
# Retires a synthetic data root the PA no longer runs on (#99): the last go-live step, once the
# real data root has run a night. It disables and removes the data root's own-filesystem mount
# units (the VM's inbox directory, the wiki, the replays), then removes the data root, L0, the
# wiki's history and the images included. Nothing of it is kept: it holds only a frozen corpus,
# which the repository holds too.
#
# It retires only a data root named like the synthetic one (PA_SYNTHETIC_DATA_ROOT), never the real
# data root or the one the scripts work on (PA_DATA_ROOT), and only while no VM is shared anything
# in it: setup-wizard.sh --go-live takes the assistant's VM off it. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($# == 1)) || die "usage: retire-data-root.sh <synthetic data root>"
refuse() {
  die "retire-data-root.sh retires only a synthetic data root, named like" \
    "$PA_SYNTHETIC_DATA_ROOT, that the PA doesn't run on ($PA_DATA_ROOT): not $1"
}
root=$(synthetic_data_root "$1") || refuse "$1"
[[ $root != "$(realpath -m -- "$PA_DATA_ROOT")" ]] || refuse "$1"

require_unshared "$root" "setup-wizard.sh --go-live does, for pa-home"
remove_own_filesystems "$root"

if [[ ! -e $root ]]; then
  log "$root is already retired"
  exit 0
fi
mounted=$(findmnt -rn -o TARGET | awk -v root="$root" 'index($0, root "/") == 1 || $0 == root')
[[ -z $mounted ]] || die "still mounted in $root: ${mounted//$'\n'/, }"
rm -rf -- "$root"
log "retired $root"
