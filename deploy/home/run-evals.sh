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
# and comes back under the data root ($PA_EVALS/runs/, or $PA_EVAL_RUNS_DIR). With --wiki DIR, a
# files-plus-wiki run over a cut asks with a dreamed copy of the cut's wiki in place of the cut's
# own (`librarian.sh dream DIR --cut CUT` leaves one in DIR, under $PA_REPLAYS), pushed as an eval
# wiki is. The cut, the set, the run directory, and that copy are refused inside this checkout,
# so nothing real is ever committed.
#
# A run over a released reproduction (--reproduction DIR: a diagnosis's reproductions/<finding>,
# or a copy of one) pushes the reproduction into the VM, replacing the last run's, and asks its
# own questions over its own payloads there, so it goes without --cut and --eval-set. A
# files-plus-wiki run asks with its starting wiki, or, with --wiki DIR, with a dreamed copy of
# it (`librarian.sh dream DIR --reproduction REPRODUCTION` leaves one in DIR, under
# $PA_REPLAYS), pushed as an eval wiki is. A reproduction is fictional, so the run directory
# comes back to evals/runs/ in this checkout (or $PA_EVAL_RUNS_DIR), as a corpus run's does.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($#)) || set -- --agent claude --judge model

# Take out the options that name devbox paths: --wiki is pointed at the VM's copy where it was
# given, and a cut and its eval set, or a reproduction, are given again at the end, as the VM's
# copies.
wiki=""
cut=""
eval_set=""
reproduction=""
args=()
while (($#)); do
  case $1 in
    --wiki | --cut | --eval-set | --reproduction)
      (($# >= 2)) || die "$1 needs a path"
      option=${1#--}
      printf -v "${option//-/_}" '%s' "$2"
      [[ $1 != --wiki ]] || args+=(--wiki "$PA_VM_EVAL_WIKI")
      shift 2
      ;;
    --wiki=* | --cut=* | --eval-set=* | --reproduction=*)
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
if [[ -n $reproduction ]]; then
  [[ -z $cut ]] || die "--reproduction goes without --cut: a run asks one or other"
  [[ -z $eval_set ]] ||
    die "--reproduction goes without --eval-set: a reproduction is asked its own questions"
  # The record a release writes (pa_evals.reproduction, which owns the rest of the layout).
  [[ -f $reproduction/reproduction.json ]] ||
    die "$reproduction isn't a released reproduction: it has no reproduction.json"
  runs_dir=${PA_EVAL_RUNS_DIR:-$REPO_DIR/evals/runs}
  args+=(--reproduction "$PA_VM_EVAL_REPRODUCTION" --runs-dir "$PA_VM_EVAL_RUNS")
elif [[ -n $cut ]]; then
  [[ -n $eval_set ]] || die "--cut needs --eval-set FILE: the real-data eval set asked over it"
  runs_dir=${PA_EVAL_RUNS_DIR:-$PA_EVALS/runs}
  require_private cut "$cut"
  require_private "eval set" "$eval_set"
  require_private "runs directory" "$runs_dir"
  # With a cut, --wiki is a dreamed copy of the cut's wiki: the owner's data, as the cut is.
  [[ -z $wiki ]] || require_private "dreamed copy of the cut's wiki" "$wiki"
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

if [[ -n $reproduction ]]; then
  # Whole, as it was released; owned by the assistant.
  vm_copy=$PA_VM_EVAL_REPRODUCTION
  tar -C "$reproduction" -cf - . |
    incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
      "rm -rf $vm_copy && mkdir -p $vm_copy && tar -C $vm_copy -xf -"
  log "pushed the reproduction in $reproduction to $vm_copy"
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
