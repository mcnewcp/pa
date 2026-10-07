"""Ingest: turning raw payloads in the inbox into episodes in L0."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pa_core.envelope import Envelope
from pa_core.errors import EpisodeConflictError, MalformedPayloadError
from pa_core.l0 import L0Store, QuarantineRecord
from pa_core.normalizers import normalizer_for


@dataclass
class IngestSummary:
    ingested: int = 0
    unchanged: int = 0
    quarantined: int = 0


def ingest(
    inbox: Path,
    store: L0Store,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IngestSummary:
    """Ingest every raw payload in `inbox`, deleting each one once it has landed.

    A payload lands as an episode in L0, or in quarantine when it cannot become one. Either
    way it is deleted from the inbox only after the write succeeds.
    """
    summary = IngestSummary()
    for payload_file in _waiting_payloads(inbox):
        raw = payload_file.read_bytes()
        at = now()
        try:
            envelope = _normalize(payload_file.name, raw, at)
        except MalformedPayloadError as error:
            store.quarantine(
                QuarantineRecord(
                    inbox_name=payload_file.name, reason=str(error), quarantined_at=at
                ),
                raw,
            )
            summary.quarantined += 1
            payload_file.unlink()
            continue
        existing = store.get(envelope.episode_id)
        if existing is None:
            store.put(envelope, raw)
            summary.ingested += 1
        elif existing.content_hash == envelope.content_hash:
            summary.unchanged += 1
        else:
            raise EpisodeConflictError(
                f"{payload_file.name}: episode {envelope.episode_id} already exists "
                "with different content"
            )
        payload_file.unlink()
    return summary


def _normalize(name: str, raw: bytes, captured_at: datetime) -> Envelope:
    return normalizer_for(name)(raw, captured_at=captured_at)


def _waiting_payloads(inbox: Path) -> list[Path]:
    """Raw payload files in the inbox, in name order; hidden files are in-progress captures."""
    return sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
