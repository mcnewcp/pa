"""Backing up with restic what can't be rebuilt cheaply: L0, the wiki's git directory, and the
private eval directory, in one snapshot.

The snapshot's root is the data root, so restoring it to a directory reproduces `l0/`,
`wiki.git/` and `evals/` there. Nothing else is backed up: the catalog is derived from L0, the
wiki's working tree is checked out from its git directory, collector state is rebuilt by
capturing again (which collapses on ingest), and neither is an envelope tree an interrupted
renormalize left behind. Nothing is ever pruned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pa_core.errors import PaError
from pa_core.status import Failure, JobStatus, Status, read_status, write_status
from pa_home.capture import failure_reason
from pa_home.config import DataRoot, require_backup_config
from pa_home.filesystem_l0 import RENORMALIZE_LEFTOVERS, FilesystemL0


class BackupError(PaError):
    """A backup that could not run or did not finish."""


def back_up(
    data_root: DataRoot,
    *,
    environ: Mapping[str, str] = os.environ,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Snapshot `data_root` into the restic repository configured in `environ`, and record the
    outcome in the status; returns the snapshot id.

    The caller holds the data root's lock, so the status and what's backed up hold still.
    """
    started = now()
    status = read_status(data_root.status)
    try:
        require_backup_config(environ)
        snapshot_id = _snapshot(data_root, environ)
    except Exception as error:
        # Only this module's and the configuration's messages are written to be shown.
        reason = str(error) if isinstance(error, PaError) else failure_reason(error)
        failure = Failure(at=started, reason=reason)
        _record(data_root, status, status.backup.model_copy(update={"last_failure": failure}), now)
        raise
    _record(data_root, status, status.backup.model_copy(update={"last_success": started}), now)
    return snapshot_id


def _record(
    data_root: DataRoot, status: Status, backup: JobStatus, now: Callable[[], datetime]
) -> None:
    write_status(data_root.status, status.model_copy(update={"backup": backup}), at=now())


def _snapshot(data_root: DataRoot, environ: Mapping[str, str]) -> str:
    restic = shutil.which("restic", path=environ.get("PATH"))
    if restic is None:
        raise BackupError("restic is not installed (or not on PATH); install it to back up.")
    if not data_root.l0.is_dir():
        raise BackupError("There is no L0 to back up; run `pa ingest` first.")
    FilesystemL0(data_root.l0)  # repairs an interrupted envelope swap, so only leftovers remain
    # The wiki and the evals come later than L0 on a fresh data root.
    paths = [
        str(path.relative_to(data_root.path))
        for path in (data_root.l0, data_root.wiki_git, data_root.evals)
        if path.exists()
    ]
    # Relative paths from inside the data root make it the root of the snapshot.
    result = subprocess.run(
        [restic, "backup", "--json", f"--exclude={RENORMALIZE_LEFTOVERS}", *paths],
        cwd=data_root.path,
        env=dict(environ),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # restic's own output can name files under the data root, so it's never passed on.
        raise BackupError(
            _FAILURES.get(
                result.returncode,
                f"restic backup failed (exit {result.returncode}); run restic by hand to see why.",
            )
        )
    return _snapshot_id(result.stdout)


# restic's exit codes (https://restic.readthedocs.io/en/stable/075_scripting.html#exit-codes).
_FAILURES = {
    3: "restic could not read some of the files, so the snapshot is incomplete.",
    10: "The restic repository is missing: is the backup drive mounted?",
    11: "The restic repository is locked by another restic process.",
    12: "The restic password doesn't open the repository.",
}


def _snapshot_id(output: str) -> str:
    for line in output.splitlines():
        message = json.loads(line)
        if message.get("message_type") == "summary":
            return message["snapshot_id"]
    raise BackupError("restic backup finished without reporting a snapshot.")
