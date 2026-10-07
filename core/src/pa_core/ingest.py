"""Ingest: turning raw payloads in the inbox into episodes in L0."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pa_core.catalog import Catalog
from pa_core.envelope import Envelope
from pa_core.errors import EpisodeConflictError, PaError
from pa_core.l0 import L0Store, QuarantineRecord
from pa_core.normalizers import normalizer_for
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

    A payload lands as an episode in L0, or in quarantine when it cannot become one. Either
    way it leaves the inbox only after that write succeeds, so input is never lost. Each
    episode is added to `catalog` as it lands.
    """
    summary = IngestSummary()
    for payload_file in _waiting_payloads(inbox):
        raw = payload_file.read_bytes()
        summary.add(_land(payload_file.name, raw, store, catalog, owner, now()))
        payload_file.unlink()
    return summary


def _land(
    inbox_name: str, raw: bytes, store: L0Store, catalog: Catalog, owner: Owner, at: datetime
) -> Outcome:
    """Write one raw payload to L0 or quarantine, never touching an existing episode."""
    try:
        envelope = normalizer_for(inbox_name, owner)(raw, captured_at=at)
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
        leftover = normalizer_for(envelope.raw_ref, owner)(raw, captured_at=at)
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


def _waiting_payloads(inbox: Path) -> list[Path]:
    """Raw payload files in the inbox, in name order; hidden files are in-progress captures."""
    return sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
