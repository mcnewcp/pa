#!/usr/bin/env bash
# Creates the instance's data root on the devbox (PA_DATA_ROOT, the real one by default), empty
# and readable only by the devbox user (mode 0700): its inbox, the VM's inbox directory, L0, the
# catalog, collector state and the status, as the scripts and the capture cycle expect them. The
# owner goes in owner.env beside them, from the wizard's owner stage (setup-wizard.sh --owner).
# Nothing is seeded: the real data root fills from the owner's own sources, by the go-live's
# backfill and the capture cycle. Safe to re-run: it keeps what the data root holds.
#
# The VMs never see the data root itself, only the directories in it that mount-store.sh and
# librarian-sandbox.sh share (virtiofsd runs as root), so its mode keeps the devbox's other
# users out but not the VMs.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($# == 0)) || die "usage: create-data-root.sh"
mkdir -p "$PA_DATA_ROOT"
chmod 0700 "$PA_DATA_ROOT"
mkdir -p "$PA_CHAT_INBOX" "$PA_L0/episodes" "$PA_DATA_ROOT"/{catalog,state} "$PA_STATUS"
log "$PA_DATA_ROOT is ready, mode 0700"
