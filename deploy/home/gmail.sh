#!/usr/bin/env bash
# Usage: gmail.sh auth
#        gmail.sh dry-run --window-start <time>
#
# Runs `pa gmail` on the devbox with this checkout's code, with Gmail's OAuth client and token in
# the owner's configuration directory (lib.sh).
#
# auth asks for the owner's consent to read Gmail, read-only, and saves the token, mode 0600. It's
# also the re-auth, for a token that died (six months unused, or a password change): `pa status`
# shows Gmail's watermark going stale and the failure saying to run it. The consent redirects to
# a loopback server on the devbox, so first forward its port from the machine with the browser:
#
#     ssh -L 8765:localhost:8765 <devbox>
#
# then open the address it prints in that machine's browser.
#
# dry-run counts what a backfill from <time> would capture, by Gmail category, from labels alone,
# without fetching any message. It prints counts only.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

case ${1:-} in
  auth)
    shift
    PA_GMAIL_CLIENT=$PA_GMAIL_CLIENT PA_GMAIL_TOKEN=$PA_GMAIL_TOKEN \
      uv run --project "$REPO_DIR" pa gmail auth --port "$PA_GMAIL_AUTH_PORT" "$@"
    ;;
  dry-run)
    require_owner_env
    set -a
    # shellcheck source=/dev/null # owner.env, written by seed-data-root.sh
    source "$PA_OWNER_ENV"
    set +a
    PA_GMAIL_TOKEN=$PA_GMAIL_TOKEN uv run --project "$REPO_DIR" pa gmail "$@"
    ;;
  *) printf 'usage: gmail.sh auth | gmail.sh dry-run --window-start <time>\n' >&2; exit 2 ;;
esac
