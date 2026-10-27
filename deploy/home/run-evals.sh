#!/usr/bin/env bash
# Usage: run-evals.sh [pa-eval run options]
#
# Runs the eval set inside the PA VM, as the assistant user, with the agent project at the VM's
# deployed commit (default options: --agent claude --judge model), under the VM's managed settings
# and sandbox, as every session there runs. The run builds its throwaway data root on the VM's own
# disk, in its eval directory, never the mounted store, with exchange capture off. The run
# directory is copied back to evals/runs/ in this checkout (or to $PA_EVAL_RUNS_DIR, if set), and
# the run fails if the VM's inbox directory changed while it ran.
#
# A files-plus-wiki run's --wiki DIR is an eval wiki in this checkout (a replay's wiki/ and
# run-log.jsonl, e.g. evals/wikis/scale/<librarian version>): its copy is pushed into the VM,
# replacing the last run's, and the run asks with that copy.
#
# A run over a cut (--cut DIR --eval-set FILE, both under the data root; `pa cut` makes the cut)
# pushes the cut into the VM once, and reuses the VM's copy on every later run over it. The eval
# set is pushed each run, replacing the last. The run directory stays out of the VM's checkout,
# and comes back under the data root ($PA_EVALS/runs/, or $PA_EVAL_RUNS_DIR). The cut, the set,
# and the run directory are refused inside this checkout, so nothing real is ever committed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($#)) || set -- --agent claude --judge model

# Take out the options that name devbox paths: --wiki is pointed at the VM's copy where it was
# given, and a cut and its eval set are given again at the end, as the VM's copies.
wiki=""
cut=""
eval_set=""
args=()
while (($#)); do
  case $1 in
    --wiki | --cut | --eval-set)
      (($# >= 2)) || die "$1 needs a path"
      option=${1#--}
      printf -v "${option//-/_}" '%s' "$2"
      [[ $1 != --wiki ]] || args+=(--wiki "$PA_VM_EVAL_WIKI")
      shift 2
      ;;
    --wiki=* | --cut=* | --eval-set=*)
      option=${1%%=*}
      option=${option#--}
      printf -v "${option//-/_}" '%s' "${1#*=}"
      [[ $option != wiki ]] || args+=(--wiki "$PA_VM_EVAL_WIKI")
      shift
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
if [[ -n $cut ]]; then
  [[ -n $eval_set ]] || die "--cut needs --eval-set FILE: the real-data eval set asked over it"
  runs_dir=${PA_EVAL_RUNS_DIR:-$PA_EVALS/runs}
  require_private cut "$cut"
  require_private "eval set" "$eval_set"
  require_private "runs directory" "$runs_dir"
  vm_cut=$PA_VM_EVAL_CUTS/$(cut_name "$cut")
  [[ -f $eval_set ]] || die "eval set not found: $eval_set"
  args+=(--cut "$vm_cut" --eval-set "$PA_VM_EVAL_SET" --runs-dir "$PA_VM_EVAL_RUNS")
else
  runs_dir=${PA_EVAL_RUNS_DIR:-$REPO_DIR/evals/runs}
  [[ -z $eval_set ]] || args+=(--eval-set "$eval_set")
  # The VM's code is root's, so the run directory can't go in its evals/runs/.
  args+=(--runs-dir "$PA_VM_EVAL_RUNS")
fi
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

if [[ -n $cut ]]; then
  push_cut "$cut"
  push_file "$eval_set" "$PA_VM_EVAL_SET"
  log "pushed the eval set to $PA_VM_EVAL_SET"
fi

before=$(inbox_listing)

output=$(mktemp)
trap 'rm -f "$output"' EXIT
as_assistant "$PA_REPO_DIR" "$PA_VM_EVAL_TMP" "$@" <<'EOF' | tee "$output"
set -euo pipefail
cd "$1"
# The run's throwaway data root and rendered projects go in TMPDIR: on the VM's disk, since
# /tmp in the VM is in memory, and outside the assistant's home, whose CLAUDE.md files the
# managed settings exclude.
export TMPDIR=$2
shift 2
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
