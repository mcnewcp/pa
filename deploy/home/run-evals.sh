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
#
# A run over a cut (--cut DIR --eval-set FILE, both under the data root; `pa cut` makes the cut)
# pushes the cut into the VM once, and reuses the VM's copy on every later run over it. The eval
# set is pushed each run, replacing the last. The run directory stays out of the VM's checkout,
# and comes back under the data root ($PA_EVALS/runs/, or $PA_EVAL_RUNS_DIR). The cut, the set,
# and the run directory are refused inside this checkout, so nothing real is ever committed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($#)) || set -- --agent claude --judge model

# require_private stops unless the path ($2) is outside this checkout, after following links;
# $1 says what it is.
require_private() {
  local resolved repository
  resolved=$(realpath -m -- "$2")
  repository=$(realpath -- "$REPO_DIR")
  case $resolved/ in
    "$repository"/*)
      die "the $1 ($resolved) is inside the repository ($repository): real-data eval sets, cuts, and run directories stay under the data root"
      ;;
  esac
}

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
  [[ -f $cut/cut.json ]] || die "$cut isn't a cut: it has no cut.json (pa cut makes one)"
  [[ -f $eval_set ]] || die "eval set not found: $eval_set"
  cut_name=$(basename "$(realpath -- "$cut")")
  [[ $cut_name =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "$cut isn't named as pa cut names a cut"
  vm_cut=$PA_VM_EVAL_CUTS/$cut_name
  args+=(--cut "$vm_cut" --eval-set "$PA_VM_EVAL_SET" --runs-dir "$PA_VM_EVAL_RUNS")
else
  runs_dir=${PA_EVAL_RUNS_DIR:-$REPO_DIR/evals/runs}
  [[ -z $eval_set ]] || args+=(--eval-set "$eval_set")
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
  # A cut never changes, so the VM's copy is kept and reused. It's unpacked beside the others
  # and moved into place whole, so a push that stopped part way is never taken for the cut.
  if incus exec "$PA_VM" -- runuser -l "$PA_USER" -c "test -d $vm_cut" </dev/null; then
    log "the cut $cut_name is already in the VM: reusing $vm_cut"
  else
    partial=$PA_VM_EVAL_CUTS/.$cut_name.partial
    tar -C "$cut" -cf - . |
      incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
        "rm -rf $partial && mkdir -p $partial && tar -C $partial -xf - && mv -T $partial $vm_cut"
    log "pushed the cut $cut_name to $vm_cut"
  fi
  incus exec "$PA_VM" -- runuser -l "$PA_USER" -c \
    "mkdir -p ${PA_VM_EVAL_SET%/*} && cat > $PA_VM_EVAL_SET" <"$eval_set"
  log "pushed the eval set to $PA_VM_EVAL_SET"
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
