"""Ingest: turning raw payloads in the inbox into episodes in L0."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pa_core.errors import EpisodeConflictError
from pa_core.l0 import L0Store
from pa_core.normalizers import normalizer_for


@dataclass
class IngestSummary:
    ingested: int = 0
    unchanged: int = 0


def ingest(
    inbox: Path,
    store: L0Store,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> IngestSummary:
    """Ingest every raw payload in `inbox`, deleting each one once it has landed."""
    summary = IngestSummary()
    for item in sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")):
        raw = item.read_bytes()
        envelope = normalizer_for(item.name)(raw, captured_at=now())
        existing = store.get(envelope.episode_id)
        if existing is None:
            store.put(envelope, raw)
            summary.ingested += 1
        elif existing.content_hash == envelope.content_hash:
            summary.unchanged += 1
        else:
            raise EpisodeConflictError(
                f"{item.name}: episode {envelope.episode_id} already exists with different content"
            )
        item.unlink()
    return summary
