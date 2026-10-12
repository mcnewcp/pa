"""Calendar events: one node per event, built from its episodes with no model call.

Each version of an event is its own episode, and every version of one event shares the event's
reference (its UID, which is also the episode's `native_id` and `thread_ref`). The event's id
comes from that reference. Each version links to the event with `VERSION_OF`, carrying that
version's title, start, end, location, and status, and when it was made. The latest version,
by its sequence number and then when it was made, is current, and its fields are the event's.

Time, title, and attendees come from the envelope, status and location from the labelled
lines the calendar normalizer writes at the top of the body, and the version's order from the
raw payload, since the envelope doesn't carry it. The event is about the topics its versions'
statements are about.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pa_core.claims.records import EdgeRecord, NodeRecord, stable_id
from pa_core.claims.schema import ABOUT, CALENDAR_EVENT, VERSION_OF
from pa_core.envelope import Envelope, Kind
from pa_core.l0 import L0Store
from pa_core.librarian.extraction import MissingPayloadError
from pa_core.normalizers.calendar import read_body, revision

SCHEDULED = "scheduled"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class Version:
    """One version of a calendar event, read from its episode."""

    envelope: Envelope
    event_id: str
    sequence: int
    changed_at: datetime
    """When this version was made; when the payload doesn't say, when it was captured."""
    title: str | None
    location: str | None
    status: str
    """`scheduled`, or `cancelled`."""

    @property
    def order(self) -> tuple[int, datetime, str]:
        """Later versions sort after earlier ones."""
        return (self.sequence, self.changed_at, self.envelope.episode_id)


def event_id(envelope: Envelope) -> str:
    """The id of the event that a calendar episode is a version of, from its reference."""
    return stable_id("calendar_event", envelope.calendar_event_ref or envelope.native_id)


def read_versions(envelopes: Iterable[Envelope], l0: L0Store) -> dict[str, Version]:
    """Every calendar episode among `envelopes` as a version of its event, by episode id."""
    versions = {}
    for envelope in envelopes:
        if envelope.kind != Kind.CALENDAR_EVENT:
            continue
        raw = l0.get_raw(envelope.raw_ref)
        if raw is None:
            raise MissingPayloadError(
                f"{envelope.episode_id} has no raw payload at {envelope.raw_ref}"
            )
        stamp = revision(raw)
        body = read_body(envelope.body)
        versions[envelope.episode_id] = Version(
            envelope=envelope,
            event_id=event_id(envelope),
            sequence=stamp.sequence,
            changed_at=stamp.modified_at or envelope.captured_at,
            title=envelope.subject,
            location=body.location,
            status=CANCELLED if body.status == CANCELLED else SCHEDULED,
        )
    return versions


def event_records(
    versions: Iterable[Version], about: Mapping[str, Sequence[str]]
) -> tuple[list[NodeRecord], list[EdgeRecord]]:
    """Each event's node in its current version, its `VERSION_OF` edges, and its `ABOUT`
    edges, from `about`: the topic ids each episode's statements are about."""
    by_event: defaultdict[str, list[Version]] = defaultdict(list)
    for version in versions:
        by_event[version.event_id].append(version)
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    for eid, history in by_event.items():
        history.sort(key=lambda version: version.order)
        current = history[-1]
        episode_ids = tuple(version.envelope.episode_id for version in history)
        nodes.append(
            NodeRecord(
                label=CALENDAR_EVENT.label,
                id=eid,
                properties=_fields(current),
                episode_ids=episode_ids,
            )
        )
        topics: defaultdict[str, list[str]] = defaultdict(list)
        for version in history:
            episode = version.envelope.episode_id
            edges.append(
                EdgeRecord(
                    label=VERSION_OF.label,
                    id=stable_id("version_of", episode, eid),
                    source=episode,
                    target=eid,
                    properties={**_fields(version), "changed_at": version.changed_at},
                    episode_ids=(episode,),
                )
            )
            for topic in about.get(episode, ()):
                if episode not in topics[topic]:
                    topics[topic].append(episode)
        edges.extend(
            EdgeRecord(
                label=ABOUT.label,
                id=stable_id("about", eid, topic),
                source=eid,
                target=topic,
                properties={},
                episode_ids=tuple(episodes),
            )
            for topic, episodes in topics.items()
        )
    return nodes, edges


def _fields(version: Version) -> dict[str, object]:
    return {
        "title": version.title,
        "starts_at": version.envelope.occurred_at.start,
        "ends_at": version.envelope.occurred_at.end,
        "location": version.location,
        "status": version.status,
    }
