#!/usr/bin/env bash
# Usage: run-evals.sh [pa-eval run options]
#
# Runs the eval set inside the PA VM, as the assistant user, with the agent project at the VM's
# deployed commit (default options: --agent claude --judge model). The run builds its throwaway
# data root on the VM's own disk, never the mounted store, with exchange capture off. The run
# directory is copied back to evals/runs/ in this checkout, and the run fails if the VM's inbox
# directory changed while it ran.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

(($#)) || set -- --agent claude --judge model

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

report=$(sed -n 's/^report: //p' "$output")
[[ -n $report ]] || die "the run didn't say where its report is"
run_dir=$(dirname "$report")
mkdir -p "$REPO_DIR/evals/runs"
incus file pull -r "$PA_VM$run_dir" "$REPO_DIR/evals/runs/"
log "copied to evals/runs/$(basename "$run_dir")"
