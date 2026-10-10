#!/usr/bin/env bash
# Seeds the instance's data root on the devbox from the frozen synthetic corpus, with the
# corpus's fictional owner as the instance owner, and ingests it with this checkout's code.
# Leaves a data root that already has an L0 alone; it is disposable, so to start over, take the
# VM's store devices off (incus config device remove pa-home pa-l0 pa-chat-inbox), delete it,
# and re-run this and mount-store.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ -d $PA_L0 ]]; then
  log "data root $PA_DATA_ROOT is seeded"
else
  log "seeding $PA_DATA_ROOT from the frozen corpus"
  mkdir -p "$PA_DATA_ROOT/inbox"
  # The corpus is laid out as the inbox is, one directory per source (ADR-0004).
  cp -r "$REPO_DIR/evals/corpus/payloads/." "$PA_DATA_ROOT/inbox/"
  uv run --project "$REPO_DIR" python - >"$PA_OWNER_ENV" <<'EOF'
import shlex

from pa_evals import frozen
from pa_evals.eval_set import load_eval_set

owner = load_eval_set(frozen.EVAL_SET).owner
for name, value in (
    ("PA_OWNER_NAME", owner.name),
    ("PA_OWNER_EMAILS", ",".join(owner.email_addresses)),
    ("PA_OWNER_OTHER_NAMES", ",".join(owner.other_names)),
):
    print(f"{name}={shlex.quote(value)}")
EOF
  "$DEPLOY_DIR/ingest.sh"
fi

# The VM's only way back; it must exist before it can be mounted.
mkdir -p "$PA_CHAT_INBOX"
