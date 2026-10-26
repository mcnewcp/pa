#!/usr/bin/env bash
# Usage: render-agent-project.sh <repo dir> <owner.env> <project> <l0> <scratch> <wiki> <status>
#
# Runs in the PA VM, as the assistant: update.sh pipes it to as_assistant. Renders the agent
# project for the owner in owner.env into <project>, reading L0 at <l0>, writing to <scratch>,
# and reading the wiki at <wiki> only when there is a wiki there: the share is mounted and the
# librarian has run into it (its index.md, which every run writes). Otherwise the assistant runs
# on files alone, as in v0.12; run update.sh again after the librarian's first run. It always
# reads the status file at <status>, even before the first capture cycle writes it: the project
# tells it what a missing status means.
set -euo pipefail
repo_dir=$1 owner_env=$2 project=$3 l0=$4 scratch=$5 wiki=$6 status=$7
set -a
# shellcheck disable=SC1090 # owner.env is the instance's
source "$owner_env"
set +a
# A rendered project is replaced, never written over. Its path stays the same, so Claude Code's
# folder trust and session history for it carry over.
rm -rf "$project"
mkdir -p "$scratch"
with_wiki=()
if [[ -f $wiki/index.md ]]; then
  with_wiki=(--wiki "$wiki")
else
  echo "no wiki at $wiki yet (no index.md): rendering the agent project for files alone" >&2
fi
"$repo_dir/.venv/bin/pa" agent-project render "$project" --l0 "$l0" --scratch "$scratch" \
  --status "$status" "${with_wiki[@]}"
