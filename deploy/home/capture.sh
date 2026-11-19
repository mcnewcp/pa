#!/usr/bin/env bash
# Usage: capture.sh [--backfill <window start> | --reconcile]
#
# Runs a capture cycle on the devbox over the instance's data root, with this checkout's code
# and the instance's configuration (lib.sh job_environment): each configured source collected
# into the inbox, ingest, and the watermarks published, under the data root's lock. A cycle
# that finds the lock held skips. pa-capture.service runs it every 15 minutes (pa-capture.timer,
# install-schedules.sh); run it by hand too, with --backfill for the go-live backfill.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

job_environment
exec "$PA_BIN" capture "$@"
