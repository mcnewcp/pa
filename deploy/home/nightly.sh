#!/usr/bin/env bash
# Runs the nightly job on the devbox over the instance's data root, with this checkout's code and
# the instance's configuration (lib.sh job_environment): one process holding the data root's lock
# throughout, which runs a capture cycle with reconciliation, the librarian run (its sessions in
# the librarian's VM, librarian-sandbox.sh), and the backup to the USB SSD's restic repository,
# each recorded in the status. It skips if another job holds the lock. pa-nightly.service runs
# it overnight (pa-nightly.timer, install-schedules.sh); run it by hand too.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

job_environment
# Its own sandbox, backup repository and password file: a librarian command or restic variables
# from the environment could run claude elsewhere, or point the backup elsewhere.
unset PA_LIBRARIAN_COMMAND RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND RESTIC_REPOSITORY_FILE
export PA_LIBRARIAN_SANDBOX=$DEPLOY_DIR/librarian-sandbox.sh
export RESTIC_REPOSITORY=$PA_BACKUP_REPOSITORY RESTIC_PASSWORD_FILE=$PA_BACKUP_PASSWORD_FILE
exec "$PA_BIN" nightly
