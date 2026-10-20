#!/usr/bin/env bash
# Usage: run-evals.sh [pa-eval run options]
#
# Runs the eval set inside the PA VM, as the assistant user, with the agent project at the VM's
# deployed commit (default options: --agent claude --judge model). The run builds its throwaway
# data root on the VM's own disk, never the mounted store, with exchange capture off. The run
# directory is copied back to evals/runs/ in this checkout (or to $PA_EVAL_RUNS_DIR, if set), and
# the run fails if the VM's inbox directory changed while it ran.
#
# A files-plus-wiki run's --wiki DIR is an eval wiki in this checkout (a replay's wiki/ and
# run-log.jsonl, e.g. evals/wikis/scale/<librarian version>): its copy is pushed into the VM,
# replacing the last run's, and the run asks with that copy.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
runs_dir=${PA_EVAL_RUNS_DIR:-$REPO_DIR/evals/runs}

(($#)) || set -- --agent claude --judge model

# Point --wiki at the VM's copy of the eval wiki, and push that copy.
wiki=""
args=()
while (($#)); do
  case $1 in
    --wiki)
      (($# >= 2)) || die "--wiki needs a directory"
      wiki=$2
      args+=(--wiki "$PA_VM_EVAL_WIKI")
      shift 2
      ;;
    --wiki=*)
      wiki=${1#--wiki=}
      args+=(--wiki "$PA_VM_EVAL_WIKI")
      shift
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
set -- "${args[@]}"
if [[ -n $wiki ]]; then
  # A replay target's layout, as pa_core.librarian.replay.ReplayTarget defines it.
  [[ -d $wiki/wiki && -f $wiki/run-log.jsonl ]] ||
    die "$wiki isn't an eval wiki: it needs wiki/ and run-log.jsonl"
  # Its tree and run log only, never a replay's wiki.git/; owned by the assistant.
  tar -C "$wiki" -cf - wiki run-log.jsonl |
    incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
      "rm -rf $PA_VM_EVAL_WIKI && mkdir -p $PA_VM_EVAL_WIKI && tar -C $PA_VM_EVAL_WIKI -xf -"
  log "pushed the eval wiki in $wiki to $PA_VM_EVAL_WIKI"
fi

# inbox_listing lists what's in the VM's inbox directory, with sizes and times.
inbox_listing() { find "$PA_CHAT_INBOX" -mindepth 1 -printf '%P %s %T@\n' | sort; }
before=$(inbox_listing)

output=$(mktemp)
trap 'rm -f "$output"' EXIT
as_assistant "$PA_REPO_DIR" "$@" <<'EOF' | tee "$output"
set -euo pipefail
cd "$1"
shift
# The run's throwaway data root and rendered projects go in TMPDIR: on the VM's disk, since
# /tmp in the VM is in memory.
export TMPDIR=$HOME/.cache/pa-eval
mkdir -p "$TMPDIR"
.venv/bin/pa-eval run "$@"
EOF

after=$(inbox_listing)
if [[ $before != "$after" ]]; then
  diff <(echo "$before") <(echo "$after") >&2 || true
  die "the eval run changed $PA_CHAT_INBOX"
fi
log "the eval run left $PA_CHAT_INBOX unchanged"

run_dir=$(sed -n 's/^run directory: //p' "$output")
[[ -n $run_dir ]] || die "the run didn't say where its run directory is"
mkdir -p "$runs_dir"
incus file pull -r "$PA_VM$run_dir" "$runs_dir/"
log "copied to $runs_dir/$(basename "$run_dir")"
