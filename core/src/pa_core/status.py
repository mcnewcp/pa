"""The status model: how current the PA is, in one file the assistant can read.

The status file holds each source's watermark and expected cadence, the last attempt and
failure of every job, and how many payloads each source has had quarantined. It never holds
content: a failure is recorded as a reason written without subjects, names, addresses, or
bodies. Each job updates its own part and writes the whole file atomically, under the data
root's lock, so a reader always sees one whole status.

The assistant judges staleness itself, against its own "now": a source is stale when its
watermark is further behind than its cadence allows. A status file that stopped updating can't
claim to be fresh, since every watermark in it falls behind.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict

from pa_core.atomic import write_atomically

STATUS_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Failure(_Model):
    """A failed attempt: when, and why, as a reason without content."""

    at: AwareDatetime
    reason: str


class SourceStatus(_Model):
    """How current one source is in L0."""

    watermark: AwareDatetime | None = None
    """The time through which the source is known to be fully captured into L0: the start of
    its last successful collection whose payloads have all landed. None until the first."""
    cadence: timedelta
    """How often the watermark is expected to move. Further behind than this, it's stale."""
    last_attempt: AwareDatetime | None = None
    """When its collector last started a collection, whether it succeeded or not."""
    last_failure: Failure | None = None
    """Its collector's most recent failure, kept after later successes."""
    quarantined: int = 0
    """How many of its payloads ingest has quarantined, ever. They don't hold the watermark
    back."""


class JobStatus(_Model):
    """A job's last success and last failure."""

    last_success: AwareDatetime | None = None
    last_failure: Failure | None = None


class LibrarianRun(_Model):
    """The outcome of the librarian's last run."""

    at: AwareDatetime
    succeeded: bool
    reason: str | None = None
    """Why it failed, without content."""
    cost_usd: float | None = None
    warnings: int | None = None
    """How many lint warnings the run left."""


class WikiStatus(_Model):
    current_through: AwareDatetime | None = None
    """The librarian's cursor after its last committed run: episodes captured after this
    aren't in the wiki yet, so they're only in L0."""
    last_run: LibrarianRun | None = None


class Status(_Model):
    version: int = STATUS_VERSION
    updated_at: AwareDatetime | None = None
    sources: dict[str, SourceStatus] = {}
    """Each collected source's status, by source name (`gmail`, `obsidian`, ...)."""
    ingest: JobStatus = JobStatus()
    wiki: WikiStatus = WikiStatus()
    backup: JobStatus = JobStatus()


def read_status(path: Path) -> Status:
    """The status file at `path`, or an empty status if none has been written yet."""
    if not path.exists():
        return Status()
    return Status.model_validate_json(path.read_bytes())


def write_status(path: Path, status: Status, *, at: datetime) -> None:
    """Replace the status file at `path` with `status`, stamped as updated `at`, in one step.

    The file is readable by everyone, so it can be shared read-only into the assistant's VM.
    """
    stamped = status.model_copy(update={"updated_at": at})
    write_atomically(path, (stamped.model_dump_json(indent=2) + "\n").encode(), mode=0o644)
