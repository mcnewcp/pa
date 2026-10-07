"""L0: the append-only store of episodes, its layout, and its storage interface (ADR-0001)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict

from pa_core.envelope import Envelope, Source


class QuarantineRecord(BaseModel):
    """Why a raw payload could not become an episode, kept beside its raw bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inbox_name: str
    reason: str
    quarantined_at: AwareDatetime


class L0Store(Protocol):
    def envelopes(self) -> Iterator[Envelope]:
        """Every episode's envelope in L0."""
        ...

    def get(self, episode_id: str) -> Envelope | None:
        """The stored envelope for `episode_id`, or None if no such episode exists."""
        ...

    def get_raw(self, raw_ref: str) -> bytes | None:
        """The raw payload stored at `raw_ref`, or None if there is none."""
        ...

    def put(self, envelope: Envelope, raw: bytes) -> None:
        """Write a new episode. Never called for an episode id that already exists."""
        ...

    def quarantine(self, record: QuarantineRecord, raw: bytes) -> None:
        """Keep a raw payload that cannot become an episode, with the record of why."""
        ...


# Episodes are partitioned by source and the UTC month of `occurred_at`. Paths are relative
# to the L0 root; raw payloads keep their native extension.


def raw_ref(source: Source, occurred_at: datetime, episode_id: str, extension: str) -> str:
    return f"raw/{source}/{_utc_month(occurred_at)}/{episode_id}{extension}"


def episode_ref(envelope: Envelope) -> str:
    month = _utc_month(envelope.occurred_at.start)
    return f"episodes/{envelope.source}/{month}/{envelope.episode_id}.json"


# Quarantined payloads keep their inbox name behind the UTC time they were quarantined and a
# digest of record and bytes, so two quarantines share a path only when both files are
# identical. The record is the same path plus `.json`.


def quarantine_ref(record: QuarantineRecord, raw: bytes) -> str:
    stamp = record.quarantined_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256(record.model_dump_json().encode("utf-8") + b"\0" + raw).hexdigest()[:8]
    return f"quarantine/{stamp}_{digest}_{record.inbox_name}"


def quarantine_record_ref(record: QuarantineRecord, raw: bytes) -> str:
    return f"{quarantine_ref(record, raw)}.json"


def _utc_month(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m")
