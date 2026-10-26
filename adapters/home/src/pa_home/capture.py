"""The capture cycle: collect every source into the inbox, ingest, and publish watermarks.

One cycle, under the data root's lock:
1. runs each collector. A collection that succeeds records a pending watermark for its source:
   the time it started. One that fails is recorded in the status, as a reason without
   content, and leaves its source's watermark where it was; the other collectors still run.
2. ingests the whole inbox, whichever collector (or the chat capture) wrote it.
3. publishes each pending watermark whose source's payloads have all landed, in L0 or in
   quarantine, and writes the status.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import AwareDatetime, BaseModel

from pa_core.catalog import open_catalog
from pa_core.envelope import Source
from pa_core.ingest import IngestSummary, ingest, waiting_payloads
from pa_core.owner import Owner
from pa_core.status import Failure, SourceStatus, read_status, write_status
from pa_home.collectors import (
    CollectionError,
    Collector,
    SourceInbox,
    read_state,
    write_state,
)
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.lock import LockHeld, data_root_lock

_PENDING = "watermarks.json"


class PendingWatermarks(BaseModel):
    """Watermarks recorded by successful collections, waiting for their payloads to land."""

    pending: dict[str, AwareDatetime] = {}


@dataclass
class CycleOutcome:
    """What one capture cycle did, in counts and reasons, never content."""

    skipped: bool = False
    """Whether the cycle found the data root's lock held, and so did nothing."""
    collected: dict[Source, int] = field(default_factory=dict)
    """How many payloads each source's collector wrote into the inbox, failed ones included."""
    failures: dict[Source, str] = field(default_factory=dict)
    """Why each failed collection failed."""
    ingested: IngestSummary | None = None
    """What ingest did; None if it failed."""
    ingest_failure: str | None = None
    published: dict[Source, datetime] = field(default_factory=dict)
    """Each watermark this cycle published."""

    @property
    def failed(self) -> bool:
        return bool(self.failures) or self.ingest_failure is not None


def capture_cycle(
    data_root: DataRoot,
    *,
    collectors: Sequence[Collector],
    owner: Owner,
    cadences: Mapping[Source, timedelta],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    window_start: datetime | None = None,
) -> CycleOutcome:
    """Run one capture cycle over `data_root`, or skip it if another run holds the lock.

    `cadences` gives each collected source's expected cadence, for the status. With
    `window_start`, the cycle is a backfill: each collector captures its source's history from
    then on instead of collecting what's new.
    """
    try:
        with data_root_lock(data_root):
            return _cycle(data_root, collectors, owner, cadences, now, window_start)
    except LockHeld:
        return CycleOutcome(skipped=True)


def _cycle(
    data_root: DataRoot,
    collectors: Sequence[Collector],
    owner: Owner,
    cadences: Mapping[Source, timedelta],
    now: Callable[[], datetime],
    window_start: datetime | None,
) -> CycleOutcome:
    outcome = CycleOutcome()
    status = read_status(data_root.status)
    sources = dict(status.sources)
    pending_file = data_root.state / _PENDING
    pending = dict(read_state(pending_file, PendingWatermarks).pending)

    for collector in collectors:
        source = collector.source
        started = now()
        inbox = SourceInbox(data_root.inbox, source)
        current = sources.get(source) or SourceStatus(cadence=cadences[source])
        state = data_root.state / source
        try:
            if window_start is None:
                collector.collect(inbox, state, now=now)
            else:
                collector.backfill(inbox, state, window_start=window_start, now=now)
        except Exception as error:
            reason = failure_reason(error)
            outcome.failures[source] = reason
            current = current.model_copy(
                update={"last_failure": Failure(at=started, reason=reason)}
            )
        else:
            pending[source] = started
        outcome.collected[source] = inbox.written
        sources[source] = current.model_copy(
            update={"last_attempt": started, "cadence": cadences[source]}
        )
    # Pending watermarks are kept before ingest, so a cycle that stops part way leaves them for
    # the next one to publish.
    write_state(pending_file, PendingWatermarks(pending=pending))

    ingest_status = status.ingest
    try:
        data_root.inbox.mkdir(parents=True, exist_ok=True)
        store = FilesystemL0(data_root.l0)
        with open_catalog(data_root.catalog, store) as catalog:
            summary = ingest(data_root.inbox, store, catalog, owner, now=now)
    except Exception as error:
        outcome.ingest_failure = failure_reason(error)
        ingest_status = ingest_status.model_copy(
            update={"last_failure": Failure(at=now(), reason=outcome.ingest_failure)}
        )
    else:
        outcome.ingested = summary
        ingest_status = ingest_status.model_copy(update={"last_success": now()})
        for name, current in sources.items():
            quarantined = summary.quarantined_from.get(name, 0)
            if quarantined:
                sources[name] = current.model_copy(
                    update={"quarantined": current.quarantined + quarantined}
                )
        for name, watermark in list(pending.items()):
            if name in sources and not waiting_payloads(data_root.inbox / name):
                sources[name] = sources[name].model_copy(update={"watermark": watermark})
                outcome.published[Source(name)] = watermark
                del pending[name]
        write_state(pending_file, PendingWatermarks(pending=pending))

    write_status(
        data_root.status,
        status.model_copy(update={"sources": sources, "ingest": ingest_status}),
        at=now(),
    )
    return outcome


def failure_reason(error: BaseException) -> str:
    """Why a job failed, without content.

    Only a CollectionError's message is written to be shown; any other error is named by its
    type alone (and an OS error's description), since its message could quote a payload.
    """
    if isinstance(error, CollectionError):
        return str(error)
    if isinstance(error, OSError) and error.strerror:
        return f"{type(error).__name__}: {error.strerror}"
    return f"unexpected {type(error).__name__}"
