#!/usr/bin/env bash
# Usage: librarian.sh run
#        librarian.sh dream [--count]
#        librarian.sh dream <copy> --corpus <dir>... | --cut <cut>
#        librarian.sh dream <copy> --reproduction <dir>
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
# dream <copy> --reproduction <dir> dreams a released reproduction's starting wiki (a
# diagnosis's reproductions/<finding>, or a copy of one), for a fault in dreaming. It hands over
# to `pa-eval reproduction dream <dir> <copy>`, since the eval harness knows a reproduction's
# layout and `pa librarian` doesn't (ADR-0010), with its sessions in the librarian's VM all the
# same. The first run makes the copy from the reproduction's wiki/, with the reproduction's
# record beside it, in a <copy> that doesn't exist yet. A later run dreams the copy as it stands,
# over the reproduction's payloads and as of its eval set's as-of time. The copy must be under
# PA_REPLAYS: that is checked here before anything is copied, and the command is given the path
# that was checked. `run-evals.sh --reproduction <dir> --wiki <copy>` then asks with it.
#
# The owner is the data root's (owner.env). A corpus has an owner of its own, who isn't the
# instance's: PA_LIBRARIAN_OWNER_ENV names a file like owner.env to take the owner from
# instead, for a command with --corpus. It goes with no other command, so the data root's own
# L0 and wiki are never run as someone else. A reproduction's owner and time zone are its eval
# set's, so that form reads no owner.env at all, and refuses PA_LIBRARIAN_OWNER_ENV.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

over_corpus=""
for_reproduction=""
for arg in "$@"; do
  case $arg in
    --corpus | --corpus=*) over_corpus=yes ;;
    --reproduction | --reproduction=*) for_reproduction=yes ;;
  esac
done

if [[ -n $for_reproduction ]]; then
  case $#:${3:-} in
    4:--reproduction) released=$4 ;;
    3:--reproduction=?*) released=${3#--reproduction=} ;;
    *) released="" ;;
  esac
  [[ -n $released && $1 == dream && $2 != -* ]] ||
    die "usage: librarian.sh dream <copy> --reproduction <dir>"
  [[ -z ${PA_LIBRARIAN_OWNER_ENV:-} ]] ||
    die "PA_LIBRARIAN_OWNER_ENV doesn't go with --reproduction: a reproduction's owner is its eval set's"
  # Checked before the command makes the copy there. The sandbox refuses a wiki on any
  # filesystem but the wiki's and the replays' (librarian-sandbox.sh start), but only once the
  # copy is made. The command gets the resolved path, so it reads no `..`, link, or `~` in it
  # another way than this check did.
  copy=$(realpath -m -- "$2")
  [[ $copy/ == "$(realpath -m -- "$PA_REPLAYS")"/?* ]] ||
    die "the copy ($copy) must be under $PA_REPLAYS, the replays' filesystem (install-wiki.sh)"
  to_run=(pa-eval reproduction dream "$released" "$copy")
else
  owner_env=$PA_OWNER_ENV
  if [[ -n ${PA_LIBRARIAN_OWNER_ENV:-} ]]; then
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
  to_run=(pa librarian "$@")
fi
# Its own PA_LIBRARIAN_COMMAND, not one from the environment, which could run claude elsewhere.
PA_DATA_DIR=$PA_DATA_ROOT PA_LIBRARIAN_SANDBOX=$DEPLOY_DIR/librarian-sandbox.sh \
  env -u PA_LIBRARIAN_COMMAND uv run --project "$REPO_DIR" "${to_run[@]}"
