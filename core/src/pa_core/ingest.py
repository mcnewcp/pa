"""Ingest: turning raw payloads in the inbox into episodes in L0."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePath, PurePosixPath

from pa_core.catalog import Catalog
from pa_core.envelope import Envelope, Source
from pa_core.errors import EpisodeConflictError, MalformedPayloadError, PaError
from pa_core.l0 import L0Store, QuarantineRecord
from pa_core.normalizers import Normalizer, normalizer_for, payload_extension
from pa_core.owner import Owner


class Outcome(StrEnum):
    INGESTED = "ingested"
    UNCHANGED = "unchanged"
    QUARANTINED = "quarantined"


@dataclass
class IngestSummary:
    ingested: int = 0
    unchanged: int = 0
    quarantined: int = 0

    def add(self, outcome: Outcome) -> None:
        match outcome:
            case Outcome.INGESTED:
                self.ingested += 1
            case Outcome.UNCHANGED:
                self.unchanged += 1
            case Outcome.QUARANTINED:
                self.quarantined += 1


def ingest(
    inbox: Path,
    store: L0Store,
    catalog: Catalog,
    owner: Owner,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IngestSummary:
    """Ingest every raw payload in `inbox`, deleting each one once it has landed.

    The inbox has one directory per source, and a payload's source is the directory it sits in,
    never anything in the payload (ADR-0004). A payload lands as an episode in L0, or in
    quarantine when it cannot become one. Either way it leaves the inbox only after that write
    succeeds, so input is never lost. Each episode is added to `catalog` as it lands.

    A symbolic link in the inbox is quarantined without reading what it points to: a writer
    confined to its inbox directory (the assistant's VM) must not be able to make ingest copy
    a file from elsewhere into L0. A payload ingest can't read is quarantined without its
    content, so the rest of the inbox still lands.
    """
    summary = IngestSummary()
    for payload_file in _waiting_payloads(inbox):
        inbox_name = payload_file.relative_to(inbox).as_posix()
        try:
            raw = _read_payload(payload_file)
        except MalformedPayloadError as error:
            summary.add(_quarantine(store, inbox_name, b"", str(error), now()))
        else:
            summary.add(_land(inbox_name, raw, store, catalog, owner, now()))
        payload_file.unlink()
    return summary


def _read_payload(path: Path) -> bytes:
    """The bytes of the regular file at `path`, never of anything a symbolic link points to.

    Raises MalformedPayloadError for a symbolic link, anything else that isn't a regular file, or
    a file the user running ingest isn't allowed to read. The link is refused when the file is
    opened, so one swapped in after the inbox was listed is refused too.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise MalformedPayloadError(
                f"{path.name}: is a symbolic link, not a payload; what it points to was not read"
            ) from error
        if error.errno in (errno.EACCES, errno.EPERM):
            raise MalformedPayloadError(
                f"{path.name}: could not be read ({error.strerror}); its content was not kept"
            ) from error
        raise
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise MalformedPayloadError(f"{path.name}: is not a regular file; it was not read")
        return file.read()


def _land(
    inbox_name: str, raw: bytes, store: L0Store, catalog: Catalog, owner: Owner, at: datetime
) -> Outcome:
    """Write one raw payload to L0 or quarantine, never touching an existing episode."""
    try:
        envelope = inbox_normalizer(PurePosixPath(inbox_name), owner)(raw, captured_at=at)
    except Exception as error:
        # A normalizer is a pure function of the payload, so any failure means the payload
        # cannot become an episode; keeping it beats stopping the rest of the inbox.
        return _quarantine(store, inbox_name, raw, _malformed_reason(error), at)
    existing = store.get(envelope.episode_id)
    if existing is None:
        try:
            store.put(envelope, raw)
        except EpisodeConflictError as error:
            # A file left in L0 by an interrupted ingest holds other bytes for this episode.
            # When they say the same thing, they are the episode's raw payload: finish it.
            leftover = _leftover_episode(store, envelope, owner, at)
            if leftover is None:
                reason = f"episode {envelope.episode_id}: {error}"
                return _quarantine(store, inbox_name, raw, reason, at)
            envelope, raw = leftover
            store.put(envelope, raw)
        catalog.add(envelope)
        return Outcome.INGESTED
    if existing.content_hash == envelope.content_hash:
        # Indexing is idempotent, and catches the catalog up when an earlier run wrote the
        # episode but stopped before cataloging it.
        catalog.add(existing)
        return Outcome.UNCHANGED
    reason = f"episode {envelope.episode_id} already exists in L0 with different content"
    return _quarantine(store, inbox_name, raw, reason, at)


def _leftover_episode(
    store: L0Store, envelope: Envelope, owner: Owner, at: datetime
) -> tuple[Envelope, bytes] | None:
    """The envelope and raw payload an interrupted ingest left at `envelope.raw_ref`, if any.

    None unless the leftover raw payload normalizes to the same content as `envelope`, so a
    recapture that differs only in volatile fields finishes the episode instead of conflicting.
    """
    raw = store.get_raw(envelope.raw_ref)
    if raw is None:
        return None
    try:
        leftover = normalizer_for(envelope.source, owner)(raw, captured_at=at)
    except Exception:
        return None
    if (leftover.episode_id, leftover.content_hash) != (envelope.episode_id, envelope.content_hash):
        return None
    return leftover, raw


def _malformed_reason(error: Exception) -> str:
    """The owner-facing message of an expected failure, else the error type and message."""
    return str(error) if isinstance(error, PaError) else f"{type(error).__name__}: {error}"


def _quarantine(store: L0Store, inbox_name: str, raw: bytes, reason: str, at: datetime) -> Outcome:
    record = QuarantineRecord(inbox_name=inbox_name, reason=reason, quarantined_at=at)
    store.quarantine(record, raw)
    return Outcome.QUARANTINED


def inbox_normalizer(inbox_name: PurePosixPath, owner: Owner) -> Normalizer:
    """The normalizer for the payload at `inbox_name`, a path within the inbox.

    The payload's directory names its source, and the payload must be in that source's one
    format. Raises MalformedPayloadError for a payload at the inbox root, in a directory that
    is not a source, or in a format its directory's source does not produce.
    """
    sources = ", ".join(f"{source}/" for source in Source)
    if len(inbox_name.parts) == 1:
        raise MalformedPayloadError(
            f"{inbox_name}: payload is at the inbox root; "
            f"payloads go in their source's directory ({sources})"
        )
    directory = inbox_name.parent.as_posix()
    if directory not in set(Source):
        raise MalformedPayloadError(
            f"{inbox_name}: inbox/{directory}/ is not a source directory ({sources})"
        )
    source = Source(directory)
    expected, found = payload_extension(source), inbox_name.suffix.lower()
    if found != expected:
        raise MalformedPayloadError(
            f"{inbox_name}: inbox/{source}/ takes '{expected}' payloads, not '{found}'"
        )
    return normalizer_for(source, owner)


def is_hidden(inbox_name: PurePath) -> bool:
    """Whether `inbox_name`, a path within the inbox, is a hidden file or in a hidden directory.

    Hidden paths are in-progress captures, never raw payloads waiting to be ingested.
    """
    return any(part.startswith(".") for part in inbox_name.parts)


def _waiting_payloads(inbox: Path) -> list[Path]:
    """Raw payload files, and symbolic links, under the inbox, in path order.

    Symbolic links are listed so they are quarantined; the walk never follows one.
    """
    entries: list[Path] = []
    for directory, subdirectories, files in inbox.walk():
        entries += [directory / name for name in (*subdirectories, *files)]
    return sorted(
        path
        for path in entries
        if (path.is_symlink() or path.is_file()) and not is_hidden(path.relative_to(inbox))
    )
