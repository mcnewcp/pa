"""Backing up L0 with restic: the raw payloads, envelopes, and quarantine, which can't be rebuilt.

The catalog lives outside L0 and is derived, so it is never backed up.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from pa_core.errors import PaError


class BackupError(PaError):
    """A backup that could not run or did not finish."""


def back_up(l0: Path, environ: Mapping[str, str] = os.environ) -> str:
    """Snapshot `l0` into the restic repository configured in `environ`; returns the snapshot id.

    The snapshot's root is L0 itself, so restoring it to a directory reproduces L0 there.
    """
    restic = shutil.which("restic", path=environ.get("PATH"))
    if restic is None:
        raise BackupError("restic is not installed (or not on PATH); install it to back up.")
    if not l0.is_dir():
        raise BackupError(f"There is no L0 to back up at {l0}; run `pa ingest` first.")
    # Backing up "." from inside L0 makes L0 the root of the snapshot.
    result = subprocess.run(
        [restic, "backup", "--json", "."],
        cwd=l0,
        env=dict(environ),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BackupError(
            f"restic backup failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return _snapshot_id(result.stdout)


def _snapshot_id(output: str) -> str:
    for line in output.splitlines():
        message = json.loads(line)
        if message.get("message_type") == "summary":
            return message["snapshot_id"]
    raise BackupError("restic backup finished without reporting a snapshot.")
