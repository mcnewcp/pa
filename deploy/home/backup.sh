#!/usr/bin/env bash
# Backs up the instance's data root on the devbox to the restic repository on the USB SSD
# (install-backup-drive.sh), with this checkout's code: one snapshot of L0, the wiki's git
# directory, and the private eval directory. `pa backup` takes the data root's lock, skipping
# if another job holds it, and records the outcome in the status. Nothing is ever pruned.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# Only the drive's repository and the password file: restic variables from the environment
# could point the backup elsewhere.
PA_DATA_DIR=$PA_DATA_ROOT RESTIC_REPOSITORY=$PA_BACKUP_REPOSITORY \
  RESTIC_PASSWORD_FILE=$PA_BACKUP_PASSWORD_FILE \
  env -u RESTIC_PASSWORD -u RESTIC_PASSWORD_COMMAND -u RESTIC_REPOSITORY_FILE \
  uv run --project "$REPO_DIR" pa backup
