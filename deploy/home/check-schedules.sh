#!/usr/bin/env bash
# Checks the schedules on the devbox (#98), with times and outcomes only, never content:
#
# - each timer is enabled and waiting, with when it last started its job and when it next will;
# - each job's last run: its result and exit status (a skip, for a held lock, is a success);
# - then `pa status`: each source's watermark and failures, the last ingest, the wiki's last run
#   with its cost and lint warnings, and the last backup.
#
# Exits 1 if a timer isn't enabled and waiting, or a job's last run failed. The journal
# (journalctl -u pa-capture -u pa-nightly) holds the same counts and reasons, and no content.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

failed=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failed=1; }

# show UNIT PROPERTY prints the unit's PROPERTY.
show() { systemctl show "$1" -p "$2" --value; }

for timer in "${PA_SCHEDULE_TIMERS[@]}"; do
  service=${timer%.timer}.service
  if systemctl is-enabled --quiet "$timer" && systemctl is-active --quiet "$timer"; then
    last=$(show "$timer" LastTriggerUSec) next=$(show "$timer" NextElapseUSecRealtime)
    # systemd works out a timer's next start only once the run it started ends.
    pass "$timer is enabled and waiting: last started ${last:-never}, next ${next:-once the run under way ends}"
  else
    fail "$timer isn't enabled and waiting: install-schedules.sh --enable"
  fi
  result=$(show "$service" Result) status=$(show "$service" ExecMainStatus)
  # systemd reports a job that has never run as a success.
  if [[ -z $(show "$service" ExecMainStartTimestamp) ]]; then
    printf '  SKIP  %s\n' "$service hasn't run yet"
  elif [[ $result == success ]]; then
    pass "$service's last run succeeded (exit $status)"
  else
    fail "$service's last run: $result (exit $status); pa status below says which step and why"
  fi
done

printf '\n'
job_environment
"$PA_BIN" status
exit "$failed"
