#!/usr/bin/env bash
# Usage: seed-data-root.sh [--corpus argus|scale] <synthetic data root>
#
# Seeds a synthetic data root on the devbox from a frozen synthetic corpus (pa_evals.frozen):
# Argus's, by default, or the scale corpus (Argus's payloads and the extension's), with the
# corpus's fictional owner as the instance owner, and ingests it with this checkout's code; the
# ingest's time is the reseed's capture time. Leaves a data root that already has an L0 alone.
#
# The data root is named, never defaulted, and must be named like the synthetic one
# (PA_SYNTHETIC_DATA_ROOT: pa-home-synthetic, or pa-home-synthetic-<anything>): the real data
# root, the deploy default, never holds a fictional owner or episode (#99). The other scripts work
# on a synthetic data root with PA_DATA_ROOT set to it. deploy/home/README.md says how to seed its
# wiki from the corpus's eval wiki afterwards (librarian.sh seed).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

usage() { die "usage: seed-data-root.sh [--corpus argus|scale] <synthetic data root>"; }
corpus=argus
case ${1:-} in
  --corpus) (($# == 3)) || usage; corpus=$2; shift 2 ;;
  --corpus=*) (($# == 2)) || usage; corpus=${1#--corpus=}; shift ;;
  *) (($# == 1)) || usage ;;
esac
[[ $corpus == argus || $corpus == scale ]] || die "--corpus takes argus or scale, not $corpus"
target=$(synthetic_data_root "$1") ||
  die "seed-data-root.sh seeds only a synthetic data root, named like $PA_SYNTHETIC_DATA_ROOT," \
    "never the real one ($PA_REAL_DATA_ROOT): not $1"
# Every path below, and ingest.sh's, follows it.
export PA_DATA_ROOT=$target
source "$DEPLOY_DIR/lib.sh"

if [[ -d $PA_L0 ]]; then
  log "data root $PA_DATA_ROOT is seeded"
else
  log "seeding $PA_DATA_ROOT from the frozen $corpus corpus"
  mkdir -p "$PA_DATA_ROOT/inbox"
  # Writes the corpus's owner to owner.env, and prints its payload directories, one per line.
  directories=$(uv run --project "$REPO_DIR" python - "$corpus" "$PA_OWNER_ENV" <<'EOF'
import shlex
import sys
from pathlib import Path

from pa_evals import frozen
from pa_evals.eval_set import load_eval_set

corpus = frozen.CORPORA[sys.argv[1]]
eval_set = load_eval_set(corpus.eval_set)
owner = eval_set.owner
lines = [
    f"{name}={shlex.quote(value)}"
    for name, value in (
        ("PA_OWNER_NAME", owner.name),
        ("PA_OWNER_EMAILS", ",".join(owner.email_addresses)),
        ("PA_OWNER_OTHER_NAMES", ",".join(owner.other_names)),
        ("PA_TIME_ZONE", eval_set.time_zone),
    )
]
Path(sys.argv[2]).write_text("".join(f"{line}\n" for line in lines))
for directory in corpus.payloads:
    print(directory)
EOF
  )
  # Each corpus directory is laid out as the inbox is, one directory per source (ADR-0004).
  while IFS= read -r directory; do
    cp -r "$directory/." "$PA_DATA_ROOT/inbox/"
  done <<<"$directories"
  "$DEPLOY_DIR/ingest.sh"
fi

# The VM's only way back; it must exist before it can be mounted.
mkdir -p "$PA_CHAT_INBOX"
