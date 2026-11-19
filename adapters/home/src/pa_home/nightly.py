"""The nightly job: one process holding the data root's lock throughout, which runs

1. a capture cycle with reconciliation, so the night's run sees what was captured up to it,
   deletions a sync report missed included;
2. the librarian run, over everything captured since its cursor;
3. the backup, which then holds the night's wiki.

It records first when the assistant's own Claude login ends, as nightly.sh found it in the
assistant's VM, so the owner hears in time to log in again. The status says the job is
running, from its start until it ends, so neither `pa status` nor
the assistant calls a source stale while the job holds the capture cycles back. Each step
records its outcome in the status, and a step that fails doesn't stop the next: the
backup runs whatever the librarian did. The one exception closes the gap the lock is for: when
the cycle's ingest failed, its payloads wait in the inbox with their capture times, and a run
now would move its cursor past them, so the run is held back (and recorded as failed) until a
cycle has landed them.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pa_core.envelope import Source
from pa_core.librarian.run import RunOutcome
from pa_core.owner import Owner
from pa_core.status import AssistantLogin, NightlyStatus, read_status, write_status
from pa_home import librarian
from pa_home.agent import wall_clock
from pa_home.backup import back_up, backup_failure_reason
from pa_home.capture import CycleOutcome, failure_reason, run_capture_cycle
from pa_home.collectors import Collector
from pa_home.config import ConfigError, DataRoot, load_librarian_config
from pa_home.lock import LockHeld, data_root_lock

LOGIN_VAR = "PA_ASSISTANT_LOGIN_EXPIRES"
"""When the assistant's login ends (ISO 8601), or `none` when it's logged out: nightly.sh reads
it in the assistant's VM."""

HELD_BACK = (
    "held back: the capture cycle before it failed to ingest, and the run would pass over "
    "what's waiting in the inbox"
)


@dataclass
class NightlyOutcome:
    """What the nightly job did, step by step, in counts and reasons, never content."""

    skipped: bool = False
    """Whether it found the data root's lock held, and so did nothing."""
    capture: CycleOutcome | None = None
    capture_failure: str | None = None
    """Why the capture cycle stopped part way, if it did (its own failures are in `capture`)."""
    librarian: RunOutcome | None = None
    librarian_failure: str | None = None
    snapshot: str | None = None
    """The backup's snapshot id."""
    backup_failure: str | None = None

    @property
    def failed(self) -> bool:
        return (
            (self.capture is not None and self.capture.failed)
            or self.capture_failure is not None
            or self.librarian_failure is not None
            or self.backup_failure is not None
        )


def nightly_job(
    data_root: DataRoot,
    *,
    collectors: Sequence[Collector],
    owner: Owner,
    cadences: Mapping[Source, timedelta],
    time_zone: ZoneInfo,
    environ: Mapping[str, str] = os.environ,
    now: Callable[[], datetime] = wall_clock,
) -> NightlyOutcome:
    """Run the night's capture cycle, librarian run, and backup under the data root's lock, or
    skip them all if another job holds it. The librarian and the backup are configured from
    `environ`."""
    try:
        with data_root_lock(data_root):
            _mark_running(data_root, now(), now)
            try:
                return _night(data_root, collectors, owner, cadences, time_zone, environ, now)
            finally:
                _mark_running(data_root, None, now)
    except LockHeld:
        return NightlyOutcome(skipped=True)


def _mark_running(data_root: DataRoot, since: datetime | None, now: Callable[[], datetime]) -> None:
    """Record in the status that the nightly job is running since `since`, or (None) isn't."""
    try:
        status = read_status(data_root.status)
        nightly = NightlyStatus(running_since=since)
        write_status(data_root.status, status.model_copy(update={"nightly": nightly}), at=now())
    except Exception:
        # A status that can't be read or written fails the steps that record in it, which
        # say why; the night carries on without the mark.
        pass


def _night(
    data_root: DataRoot,
    collectors: Sequence[Collector],
    owner: Owner,
    cadences: Mapping[Source, timedelta],
    time_zone: ZoneInfo,
    environ: Mapping[str, str],
    now: Callable[[], datetime],
) -> NightlyOutcome:
    outcome = NightlyOutcome()
    _record_login(data_root, environ.get(LOGIN_VAR), now)
    try:
        outcome.capture = run_capture_cycle(
            data_root,
            collectors=collectors,
            owner=owner,
            cadences=cadences,
            now=now,
            reconcile=True,
        )
    except Exception as error:
        outcome.capture_failure = failure_reason(error)

    try:
        if outcome.capture is None or outcome.capture.ingest_failure is not None:
            _not_run(outcome, data_root, HELD_BACK, now)
        else:
            try:
                config = load_librarian_config(environ)
            except ConfigError as error:
                _not_run(outcome, data_root, str(error), now)
            else:
                outcome.librarian = librarian.run(
                    data_root, owner=owner, time_zone=time_zone, config=config, now=now
                )
    except Exception as error:
        # librarian.run recorded it; a reason given already stands.
        outcome.librarian_failure = outcome.librarian_failure or librarian.run_failure_reason(error)

    try:
        outcome.snapshot = back_up(data_root, environ=environ, now=now)
    except Exception as error:
        outcome.backup_failure = backup_failure_reason(error)
    return outcome


def _record_login(data_root: DataRoot, given: str | None, now: Callable[[], datetime]) -> None:
    """Record when the assistant's login ends, as `given` (`LOGIN_VAR`). Without a time given,
    or one with no offset, the VM didn't say: what was recorded stands."""
    try:
        expires = None if given == "none" else datetime.fromisoformat(given or "")
    except ValueError:
        return
    if expires is not None and expires.utcoffset() is None:
        return
    try:
        status = read_status(data_root.status)
        login = AssistantLogin(checked_at=now(), expires_at=expires)
        write_status(
            data_root.status, status.model_copy(update={"assistant_login": login}), at=now()
        )
    except Exception:
        # As for the running mark: the steps after it say why the status can't be written.
        pass


def _not_run(
    outcome: NightlyOutcome, data_root: DataRoot, reason: str, now: Callable[[], datetime]
) -> None:
    outcome.librarian_failure = reason
    librarian.record_run_not_made(data_root, reason, now=now)
