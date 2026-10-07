"""Ingest: turning raw payloads in the inbox into episodes in L0."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pa_core.errors import EpisodeConflictError, MalformedPayloadError
from pa_core.l0 import L0Store, QuarantineRecord
from pa_core.normalizers import normalizer_for


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
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IngestSummary:
    """Ingest every raw payload in `inbox`, deleting each one once it has landed.

    A payload lands as an episode in L0, or in quarantine when it cannot become one. Either
    way it leaves the inbox only after that write succeeds, so input is never lost.
    """
    summary = IngestSummary()
    for payload_file in _waiting_payloads(inbox):
        raw = payload_file.read_bytes()
        summary.add(_land(payload_file.name, raw, store, now()))
        payload_file.unlink()
    return summary


def _land(name: str, raw: bytes, store: L0Store, at: datetime) -> Outcome:
    """Write one raw payload to L0 or quarantine, never touching an existing episode."""
    try:
        envelope = normalizer_for(name)(raw, captured_at=at)
    except MalformedPayloadError as error:
        return _quarantine(store, name, raw, str(error), at)
    existing = store.get(envelope.episode_id)
    if existing is None:
        try:
            store.put(envelope, raw)
        except EpisodeConflictError as error:
            # A file left in L0 by an interrupted ingest holds other bytes for this episode.
            return _quarantine(store, name, raw, f"episode {envelope.episode_id}: {error}", at)
        return Outcome.INGESTED
    if existing.content_hash == envelope.content_hash:
        return Outcome.UNCHANGED
    reason = f"episode {envelope.episode_id} already exists in L0 with different content"
    return _quarantine(store, name, raw, reason, at)


def _quarantine(store: L0Store, name: str, raw: bytes, reason: str, at: datetime) -> Outcome:
    store.quarantine(QuarantineRecord(inbox_name=name, reason=reason, quarantined_at=at), raw)
    return Outcome.QUARANTINED


def _waiting_payloads(inbox: Path) -> list[Path]:
    """Raw payload files in the inbox, in name order; hidden files are in-progress captures."""
    return sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
