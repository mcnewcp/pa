#!/usr/bin/env bash
# Ingests the instance's inbox into its L0 on the devbox, with this checkout's code. In v0.11
# ingest runs by hand: this is how exchanges the VM captured land in L0.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -f $PA_OWNER_ENV ]] || die "$PA_OWNER_ENV is missing: run seed-data-root.sh first"
set -a
# shellcheck source=/dev/null # owner.env, written by seed-data-root.sh
source "$PA_OWNER_ENV"
set +a
PA_DATA_DIR=$PA_DATA_ROOT uv run --project "$REPO_DIR" pa ingest
