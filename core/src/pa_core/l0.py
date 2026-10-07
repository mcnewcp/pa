"""L0: the append-only store of episodes, its layout, and its storage interface (ADR-0001)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pa_core.envelope import Envelope, Source


class L0Store(Protocol):
    def get(self, episode_id: str) -> Envelope | None:
        """The stored envelope for `episode_id`, or None if no such episode exists."""
        ...

    def put(self, envelope: Envelope, raw: bytes) -> None:
        """Write a new episode. Never called for an episode id that already exists."""
        ...


# Episodes are partitioned by source and the UTC month of `occurred_at`. Paths are relative
# to the L0 root; raw payloads keep their native extension.


def raw_ref(source: Source, occurred_at: datetime, episode_id: str, extension: str) -> str:
    return f"raw/{source}/{_utc_month(occurred_at)}/{episode_id}{extension}"


def episode_ref(envelope: Envelope) -> str:
    month = _utc_month(envelope.occurred_at.start)
    return f"episodes/{envelope.source}/{month}/{envelope.episode_id}.json"


def _utc_month(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m")
