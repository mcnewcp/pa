"""Batches: which episodes a librarian run offers, each with the day it belongs to.

A production batch is the episodes captured since the run cursor, ordered by capture time, then
episode id. The cursor is the last episode a committed run took, so a run that fails leaves its
episodes new for the next.

Episode selection (`pa_core.selection`) decides which of them are offered:

- Promotions and Social mail never;
- Updates and Forums mail, flagged for the librarian to judge;
- a daily note only in its latest version, judged over all of L0, with the versions it
  supersedes;
- a chat with its owner turns, read from its raw payload: they are its only evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from pa_core.envelope import Envelope, Kind, Participant, Source
from pa_core.errors import PaError
from pa_core.l0 import L0Store, episode_ref
from pa_core.normalizers.assistant_chat import owner_turns
from pa_core.selection import CHECKED, SKIPPED, category, latest_note_versions


@dataclass(frozen=True, order=True)
class Cursor:
    """Where the last committed run stopped: its last episode by capture time, then id."""

    captured_at: datetime
    episode_id: str

    def __str__(self) -> str:
        return f"{self.captured_at.astimezone(UTC).isoformat()} {self.episode_id}"

    @classmethod
    def parse(cls, text: str) -> Cursor:
        """The cursor `str(cursor)` wrote. Raises ValueError for anything else."""
        moment, _, episode_id = text.strip().partition(" ")
        captured_at = datetime.fromisoformat(moment)
        if captured_at.utcoffset() is None or not episode_id:
            raise ValueError(f"not a cursor: {text!r}")
        return cls(captured_at, episode_id)


@dataclass(frozen=True)
class BatchEpisode:
    """One episode offered to the librarian, with what the host knows about it."""

    episode_id: str
    episode_ref: str
    """Where the episode's envelope is, relative to L0's root."""
    source: Source
    kind: Kind
    day: date
    """The day log it belongs in: when it occurred, in the owner's time zone."""
    subject: str | None
    participants: tuple[Participant, ...]
    judge_relevance: bool = False
    """Mail that may not bear on the owner's own dealings (Updates, Forums): the librarian
    judges whether it's relevant."""
    owner_turns: tuple[str, ...] | None = None
    """A chat's owner turns, its only evidence; None for anything but a chat."""
    supersedes: tuple[str, ...] = ()
    """The earlier versions of a daily note that this one replaces."""


@dataclass(frozen=True)
class Batch:
    """The episodes captured since the cursor, and those of them offered to the librarian."""

    episodes: tuple[BatchEpisode, ...]
    """The episodes offered, by capture time, then id."""
    captured: int
    """How many episodes were captured since the cursor, offered or not."""
    first_captured: datetime
    last_captured: datetime
    cursor: Cursor
    """Where the next run starts once this one is committed."""


def production_batch(store: L0Store, *, after: Cursor | None, time_zone: tzinfo) -> Batch | None:
    """The episodes captured since `after` (all of L0 without one), or None if there are none."""
    everything = list(store.envelopes())
    captured = sorted(
        (e for e in everything if after is None or _position(e) > after), key=_position
    )
    if not captured:
        return None
    latest = {e.episode_id for e in latest_note_versions(everything)}
    notes: dict[str, list[str]] = {}
    for envelope in sorted((e for e in everything if e.kind is Kind.NOTE), key=_position):
        notes.setdefault(envelope.native_id, []).append(envelope.episode_id)
    offered = [
        _offered(e, time_zone, store, notes)
        for e in captured
        if e.episode_id in latest and category(e) not in SKIPPED
    ]
    return Batch(
        episodes=tuple(offered),
        captured=len(captured),
        first_captured=captured[0].captured_at,
        last_captured=captured[-1].captured_at,
        cursor=_position(captured[-1]),
    )


def episode_day(envelope: Envelope, time_zone: tzinfo) -> date:
    """The day `envelope` occurred on, in `time_zone`.

    Something that spans whole days from a midnight (a daily note, an all-day event) occurred
    on its own date, whatever the time zone: a note for 5 Oct starts at midnight UTC, which is
    still 4 Oct in Chicago.
    """
    span = envelope.occurred_at
    if span.end is not None and _whole_days(span.start, span.end):
        return span.start.date()
    return span.start.astimezone(time_zone).date()


def _whole_days(start: datetime, end: datetime) -> bool:
    length = end - start
    return (
        start.time() == time()
        and length >= timedelta(days=1)
        and length % timedelta(days=1) == timedelta()
    )


def _offered(
    envelope: Envelope, time_zone: tzinfo, store: L0Store, notes: dict[str, list[str]]
) -> BatchEpisode:
    supersedes: tuple[str, ...] = ()
    if envelope.kind is Kind.NOTE:
        versions = notes[envelope.native_id]
        supersedes = tuple(versions[: versions.index(envelope.episode_id)])
    return BatchEpisode(
        episode_id=envelope.episode_id,
        episode_ref=episode_ref(envelope),
        source=envelope.source,
        kind=envelope.kind,
        day=episode_day(envelope, time_zone),
        subject=envelope.subject,
        participants=tuple(envelope.participants),
        judge_relevance=envelope.kind is Kind.EMAIL and category(envelope) in CHECKED,
        owner_turns=_owner_turns(envelope, store) if envelope.kind is Kind.CHAT else None,
        supersedes=supersedes,
    )


def _owner_turns(envelope: Envelope, store: L0Store) -> tuple[str, ...]:
    raw = store.get_raw(envelope.raw_ref)
    if raw is None:
        raise PaError(f"{envelope.episode_id}'s raw payload is missing from L0")
    return tuple(owner_turns(raw))


def _position(envelope: Envelope) -> Cursor:
    return Cursor(envelope.captured_at, envelope.episode_id)
