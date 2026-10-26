#!/usr/bin/env bash
# Usage: draft-evals.sh --cut DIR [--revise DRAFT --notes FILE] [pa-eval draft options]
#
# Has a session in the PA VM draft the real-data eval set from a cut (`pa cut` makes one), as the
# assistant user, for the owner to correct. The session reads the cut's L0, never its wiki, and
# runs with exchange capture off; it drafts as the owner in the VM's owner.env. The draft comes
# back to $PA_EVALS/drafts/<cut>/draft-<UTC time>.json (or under $PA_EVAL_DRAFTS_DIR/<cut>/), mode
# 0600, and the run fails if the VM's inbox directory changed while it ran.
#
# A second pass revises a draft, as the owner left it, by their notes: --revise DRAFT --notes FILE.
# Both are pushed into the VM, and the revision comes back as a new draft beside the others; no
# draft is ever written over.
#
# The cut is pushed into the VM once and reused, as run-evals.sh does. The cut, the drafts, and
# the notes are refused inside this checkout, so nothing real is ever committed. Default options:
# --agent claude.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# Take out the options that name devbox paths; they're given again as the VM's copies.
cut=""
revise=""
notes=""
args=()
while (($#)); do
  case $1 in
    --cut | --revise | --notes)
      (($# >= 2)) || die "$1 needs a path"
      printf -v "${1#--}" '%s' "$2"
      shift 2
      ;;
    --cut=* | --revise=* | --notes=*)
      option=${1%%=*}
      printf -v "${option#--}" '%s' "${1#*=}"
      shift
      ;;
    --out | --out=*) die "draft-evals.sh names the draft itself: drop --out" ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
[[ -n $cut ]] || die "--cut DIR is needed: the cut to draft from (pa cut makes one)"
[[ -z $revise && -z $notes ]] || [[ -n $revise && -n $notes ]] ||
  die "a second pass needs both --revise DRAFT and --notes FILE"
[[ " ${args[*]-} " == *" --agent "* || " ${args[*]-} " == *" --agent="* ]] ||
  args=(--agent claude "${args[@]}")

require_private cut "$cut"
drafts_dir=${PA_EVAL_DRAFTS_DIR:-$PA_EVALS/drafts}/$(cut_name "$cut")
require_private "drafts directory" "$drafts_dir"
name=draft-$(date -u +%Y%m%dT%H%M%SZ).json
[[ ! -e $drafts_dir/$name ]] || die "$drafts_dir/$name already exists: wait a second and retry"
vm_draft=$PA_VM_EVAL_DRAFTS/$name
args+=(--cut "$PA_VM_EVAL_CUTS/$(cut_name "$cut")" --out "$vm_draft")
if [[ -n $revise ]]; then
  require_private draft "$revise"
  require_private notes "$notes"
  [[ -f $revise ]] || die "draft not found: $revise"
  [[ -f $notes ]] || die "notes not found: $notes"
  args+=(--revise "$PA_VM_EVAL_DRAFTS/revise.json" --notes "$PA_VM_EVAL_DRAFTS/notes.md")
fi

push_cut "$cut"
if [[ -n $revise ]]; then
  push_file "$revise" "$PA_VM_EVAL_DRAFTS/revise.json"
  push_file "$notes" "$PA_VM_EVAL_DRAFTS/notes.md"
  log "pushed the draft and the notes to $PA_VM_EVAL_DRAFTS"
fi

before=$(inbox_listing)

# Only what the draft amounts to is printed (its question and episode counts), never what it says.
as_assistant "$PA_REPO_DIR" "$PA_VM_OWNER_ENV" "${args[@]}" <<'EOF'
set -euo pipefail
cd "$1"
owner_env=$2
shift 2
set -a
# shellcheck disable=SC1090 # owner.env is the instance's
source "$owner_env"
set +a
# The session's throwaway copy of the cut and its rendered project go in TMPDIR: on the VM's disk,
# since /tmp in the VM is in memory.
export TMPDIR=$HOME/.cache/pa-eval
mkdir -p "$TMPDIR"
.venv/bin/pa-eval draft "$@"
EOF

after=$(inbox_listing)
if [[ $before != "$after" ]]; then
  diff <(echo "$before") <(echo "$after") >&2 || true
  die "the drafting session changed $PA_CHAT_INBOX"
fi
log "the drafting session left $PA_CHAT_INBOX unchanged"

mkdir -p "$drafts_dir"
chmod 0700 "$drafts_dir"
incus file pull "$PA_VM$vm_draft" "$drafts_dir/$name"
chmod 0600 "$drafts_dir/$name"
log "the draft is in $drafts_dir/$name"
