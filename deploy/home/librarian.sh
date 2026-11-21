#!/usr/bin/env bash
# Usage: librarian.sh run
#        librarian.sh dream [--count]
#        librarian.sh dream <copy> --corpus <dir>... | --cut <cut>
#        librarian.sh replay <target> [<option>...]
#        librarian.sh seed <eval wiki> [--captured-through <time>]
#
# Runs `pa librarian` on the devbox over the instance's data root, with this checkout's code,
# its sessions in the librarian's VM (librarian-sandbox.sh): started for the run (or the whole
# replay) and stopped after it. A replay's target must be under PA_REPLAYS, the replays'
# filesystem (install-wiki.sh). In v0.12.1 runs start by hand: after ingest.sh, say. dream
# reviews the whole wiki once, as one commit of its own; with --count it runs nothing and
# prints what a run would find, in counts alone. seed runs no session: it starts the
# data root's fresh wiki from an eval wiki, after a reseed.
#
# dream <copy> reviews a copy instead of the data root's wiki, at the copy's as-of time: a copy
# of an eval wiki (its wiki/ and run-log.jsonl) with the corpus it was replayed over, or a copy
# of a cut's wiki (as <copy>/wiki) with the cut. The copy must be under PA_REPLAYS too.
#
# The owner is the data root's (owner.env). A corpus has an owner of its own, who isn't the
# instance's: PA_LIBRARIAN_OWNER_ENV names a file like owner.env to take the owner from
# instead, for a command with --corpus. It goes with no other command, so the data root's own
# L0 and wiki are never run as someone else.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

owner_env=$PA_OWNER_ENV
if [[ -n ${PA_LIBRARIAN_OWNER_ENV:-} ]]; then
  over_corpus=""
  for arg in "$@"; do
    [[ $arg != --corpus && $arg != --corpus=* ]] || over_corpus=yes
  done
  [[ -n $over_corpus ]] ||
    die "PA_LIBRARIAN_OWNER_ENV goes with --corpus: only a corpus has an owner who isn't the data root's"
  owner_env=$PA_LIBRARIAN_OWNER_ENV
  [[ -f $owner_env ]] || die "$owner_env is missing: PA_LIBRARIAN_OWNER_ENV names a file like owner.env"
else
  require_owner_env
fi
set -a
# shellcheck source=/dev/null # owner.env, written by the wizard's owner stage
source "$owner_env"
set +a
# Its own PA_LIBRARIAN_COMMAND, not one from the environment, which could run claude elsewhere.
PA_DATA_DIR=$PA_DATA_ROOT PA_LIBRARIAN_SANDBOX=$DEPLOY_DIR/librarian-sandbox.sh \
  env -u PA_LIBRARIAN_COMMAND uv run --project "$REPO_DIR" pa librarian "$@"
