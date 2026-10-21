#!/usr/bin/env bash
# Usage: librarian.sh run
#        librarian.sh replay <target> [<option>...]
#        librarian.sh seed <eval wiki> [--captured-through <time>]
#
# Runs `pa librarian` on the devbox over the instance's data root, with this checkout's code,
# its sessions in the librarian's VM (librarian-sandbox.sh): started for the run (or the whole
# replay) and stopped after it. A replay's target must be under PA_REPLAYS, the replays'
# filesystem (install-wiki.sh). In v0.12.1 runs start by hand: after ingest.sh, say. seed runs
# no session: it starts the data root's fresh wiki from an eval wiki, after a reseed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_owner_env
set -a
# shellcheck source=/dev/null # owner.env, written by seed-data-root.sh
source "$PA_OWNER_ENV"
set +a
# Its own PA_LIBRARIAN_COMMAND, not one from the environment, which could run claude elsewhere.
PA_DATA_DIR=$PA_DATA_ROOT PA_LIBRARIAN_SANDBOX=$DEPLOY_DIR/librarian-sandbox.sh \
  env -u PA_LIBRARIAN_COMMAND uv run --project "$REPO_DIR" pa librarian "$@"
