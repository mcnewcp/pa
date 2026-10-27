"""The one lock on the data root.

Every job that writes the store takes it: a capture cycle holds it from its first collector
through ingest and publication, and the nightly job holds it throughout. A job that finds it
held skips rather than waits, so scheduled runs never pile up.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager

from pa_core.errors import PaError
from pa_home.config import DataRoot

LOCK_HELD = "another job holds the data root's lock"
"""Why a job skipped or refused: a capture cycle, the nightly job, or a command holds the lock."""


def skipped(command: str) -> str:
    """What `command` prints when it skips because the lock is held."""
    return f"{command}: skipped: {LOCK_HELD}"


class LockHeld(PaError):
    """Another job holds the data root's lock."""


@contextmanager
def data_root_lock(data_root: DataRoot) -> Iterator[None]:
    """Hold the data root's lock for the block; raises LockHeld at once if another job has it.

    The lock is the kernel's, on an open file, so it's released when its holder exits, however
    it exits.
    """
    data_root.path.mkdir(parents=True, exist_ok=True)
    with data_root.lock.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(LOCK_HELD) from None
        yield
