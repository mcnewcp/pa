"""Batches: which episodes a librarian run offers, each with the day it belongs to.

A production batch is the episodes captured since the run cursor, ordered by capture time, then
episode id. The cursor is the last episode a committed run took, so a run that fails leaves its
episodes new for the next.

A replay batch is the episodes whose day falls in a window of whole days (a week, in a replay),
ordered by when they occurred, then episode id. Replays batch by occurrence because eval ingest
gives almost every episode the same capture time. A windowed replay's first window starts at
the window start, which may fall within a day: what occurred before it is never offered.

Episode selection (`pa_core.selection`) decides which of them are offered:

- Promotions and Social mail never;
- Updates and Forums mail, flagged for the librarian to judge;
- a daily note only in its latest version, judged over all of L0, with the versions it
  supersedes;
- a chat with its owner turns, read from its raw payload: they are its only evidence.

Known limitation: a daily note's capture time comes from its own capture header, so a note
ingested after a run but captured (by its header) before that run's cursor is never offered.
That's fine for the frozen corpora; real collectors (v0.13) need to revisit it.
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
class Window:
    """When a replay batch's episodes occurred: from `start` up to the day of `end`.

    Both are midnights in the owner's time zone, except that a windowed replay's first window
    starts at the window start, and a replay's last window ends at the replay's as-of time if
    it has one. The batch's sessions are told `end` is now.
    """

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Batch:
    """The episodes a run takes (captured since the cursor, or occurred in a replay's window),
    and those of them offered to the librarian."""

    episodes: tuple[BatchEpisode, ...]
    """The episodes offered: by capture time, then id; in a replay, by occurrence, then id."""
    captured: int
    """How many episodes the batch took, offered or not."""
    first_captured: datetime
    last_captured: datetime
    cursor: Cursor
    """Where the next production run starts once this one is committed."""
    window: Window | None = None
    """A replay batch's window; None for a production batch."""


def production_batch(store: L0Store, *, after: Cursor | None, time_zone: tzinfo) -> Batch | None:
    """The episodes captured since `after` (all of L0 without one), or None if there are none."""
    everything = list(store.envelopes())
    captured = sorted(
        (e for e in everything if after is None or _position(e) > after), key=_position
    )
    if not captured:
        return None
    return Batch(
        episodes=_offered_of(captured, everything, time_zone, store),
        captured=len(captured),
        first_captured=captured[0].captured_at,
        last_captured=captured[-1].captured_at,
        cursor=_position(captured[-1]),
    )


def cursor_through(store: L0Store, captured_through: datetime | None = None) -> Cursor | None:
    """The cursor of a run that took every episode captured up to `captured_through` (all of
    L0 without it): the last of them by capture time, then id. None if there are none."""
    taken = [
        _position(e)
        for e in store.envelopes()
        if captured_through is None or e.captured_at <= captured_through
    ]
    return max(taken, default=None)


def replay_batch(
    store: L0Store, window: Window, *, time_zone: tzinfo, last: bool = False
) -> Batch | None:
    """The episodes whose day (in `time_zone`) falls in `window`, or None if there are none.
    None that occurred before the window's start is taken, even on its day.

    The `last` window of a replay also takes every episode after it (a calendar event booked
    for after the as-of time). The cursor is the last episode by capture time of everything up
    to the window's end, so a replayed wiki carries on in production where the replay stopped.
    """
    everything = list(store.envelopes())
    end = window.end.astimezone(time_zone).date()
    days = {e.episode_id: episode_day(e, time_zone) for e in everything}
    taken = sorted(
        (
            e
            for e in everything
            if _occurred_from(e, time_zone) >= window.start and (last or days[e.episode_id] < end)
        ),
        key=lambda e: (e.occurred_at.start, e.episode_id),
    )
    if not taken:
        return None
    replayed = [e for e in everything if last or days[e.episode_id] < end]
    return Batch(
        episodes=_offered_of(taken, everything, time_zone, store),
        captured=len(taken),
        first_captured=min(e.captured_at for e in taken),
        last_captured=max(e.captured_at for e in taken),
        cursor=max(_position(e) for e in replayed),
        window=window,
    )


def _offered_of(
    taken: list[Envelope], everything: list[Envelope], time_zone: tzinfo, store: L0Store
) -> tuple[BatchEpisode, ...]:
    """The episodes of `taken` offered to the librarian, judged over `everything` in L0."""
    latest = {e.episode_id for e in latest_note_versions(everything)}
    notes: dict[str, list[str]] = {}
    for envelope in sorted((e for e in everything if e.kind is Kind.NOTE), key=_position):
        notes.setdefault(envelope.native_id, []).append(envelope.episode_id)
    return tuple(
        _offered(e, time_zone, store, notes)
        for e in taken
        if e.episode_id in latest and category(e) not in SKIPPED
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


def _occurred_from(envelope: Envelope, time_zone: tzinfo) -> datetime:
    """When `envelope` occurred, for a window's start: the midnight (in `time_zone`) of its
    day when it spans whole days (see `episode_day`), else when it started."""
    span = envelope.occurred_at
    if span.end is not None and _whole_days(span.start, span.end):
        return datetime.combine(span.start.date(), time(), tzinfo=time_zone)
    return span.start


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
