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

inbox=$PA_DATA_ROOT/inbox/assistant_chat
before=$(find "$inbox" -mindepth 1 -printf '%P %s %T@\n' | sort)

output=$(mktemp)
trap 'rm -f "$output"' EXIT
as_assistant "$PA_REPO_DIR" "$@" <<'EOF' | tee "$output"
set -euo pipefail
cd "$1"
shift
.venv/bin/pa-eval run "$@"
EOF

after=$(find "$inbox" -mindepth 1 -printf '%P %s %T@\n' | sort)
if [[ $before != "$after" ]]; then
  diff <(echo "$before") <(echo "$after") >&2 || true
  die "the eval run changed $inbox"
fi
log "the eval run left $inbox unchanged"

report=$(sed -n 's/^report: //p' "$output")
[[ -n $report ]] || die "the run didn't say where its report is"
run_dir=$(dirname "$report")
mkdir -p "$REPO_DIR/evals/runs"
incus file pull -r "$PA_VM$run_dir" "$REPO_DIR/evals/runs/"
log "copied to evals/runs/$(basename "$run_dir")"
