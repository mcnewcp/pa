#!/usr/bin/env bash
# Usage: calendar.sh calendars
#        calendar.sh dry-run --window-start <time>
#
# Runs `pa calendar` on the devbox with this checkout's code, with the Apple ID and the calendars
# from icloud.env and the app-specific password from the owner's configuration directory
# (lib.sh). setup-wizard.sh --icloud writes them.
#
# calendars names the calendars the account serves, marking those PA_ICLOUD_CALENDARS captures.
# It's also the check that the app-specific password works: a wrong one fails with HTTP 401.
#
# dry-run counts, per calendar, what a backfill from <time> would take: every resource is read,
# and nothing but counts is printed.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ -f $PA_ICLOUD_ENV ]] || die "$PA_ICLOUD_ENV is missing: run setup-wizard.sh --icloud first"
set -a
# shellcheck source=/dev/null # icloud.env, written by the wizard
source "$PA_ICLOUD_ENV"
set +a
export PA_ICLOUD_PASSWORD_FILE

case ${1:-} in
  calendars)
    uv run --project "$REPO_DIR" pa calendar calendars
    ;;
  dry-run)
    require_owner_env
    set -a
    # shellcheck source=/dev/null # owner.env, written by the wizard's owner stage
    source "$PA_OWNER_ENV"
    set +a
    uv run --project "$REPO_DIR" pa calendar "$@"
    ;;
  *) printf 'usage: calendar.sh calendars | calendar.sh dry-run --window-start <time>\n' >&2; exit 2 ;;
esac
