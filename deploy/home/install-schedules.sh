#!/usr/bin/env bash
# Installs the schedules on the devbox (#98): pa-capture.service and its timer, every 15 minutes,
# and pa-nightly.service and its timer, overnight, as system units run as the devbox user
# (schedules/, rendered by lib.sh render_schedule_unit):
#
#   install-schedules.sh            install or update the units; enabled timers carry on with them
#   install-schedules.sh --enable   then enable and start both timers
#
# The services run this checkout's code (PA_BIN, the venv uv sync makes), so install from the
# checkout the devbox keeps. --enable refuses until the wiki is seeded: a capture cycle between
# the go-live backfill and the seed would be both replayed and offered again. Safe to re-run.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

enable=false
case ${1:-} in
  "") ;;
  --enable) enable=true ;;
  *) die "usage: install-schedules.sh [--enable]" ;;
esac
require_owner_env
[[ -x $PA_BIN ]] || die "$PA_BIN is missing: run uv sync in $REPO_DIR"

# What the services may write must exist before they start: a missing path stays read-only.
mkdir -p "$PA_DATA_ROOT"/{inbox,l0,catalog,state,status} "$PA_RESTIC_CACHE"
touch "$PA_DATA_ROOT/pa.lock"

changed=false
rendered=$(mktemp)
trap 'rm -f "$rendered"' EXIT
for unit in "${PA_SCHEDULE_UNITS[@]}"; do
  render_schedule_unit "$unit" >"$rendered"
  if ! cmp -s "$rendered" "$PA_SYSTEMD_DIR/$unit"; then
    sudo install -m 0644 "$rendered" "$PA_SYSTEMD_DIR/$unit"
    log "installed $PA_SYSTEMD_DIR/$unit"
    changed=true
  fi
done
if $changed; then sudo systemctl daemon-reload; fi

if $enable; then
  [[ -d $PA_WIKI_GIT ]] ||
    die "the wiki isn't seeded yet ($PA_WIKI_GIT is missing): backfill and seed it first"
  sudo systemctl enable --now "${PA_SCHEDULE_TIMERS[@]}"
fi
for timer in "${PA_SCHEDULE_TIMERS[@]}"; do
  if systemctl is-enabled --quiet "$timer"; then
    log "$timer is enabled"
  else
    log "$timer is installed but not enabled: install-schedules.sh --enable starts it"
  fi
done
