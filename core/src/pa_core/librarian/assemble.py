"""Assembly: turning episodes and their extractions into the graph's node and edge records.

This is where who said what is decided. A firsthand statement's speaker is the episode's
sender, author (the owner, for a daily note or a chat), or organizer (for a calendar event),
from its metadata, whatever the model wrote; a statement whose quote is not in the episode's
quotable texts (for a chat, one of the owner's turns) is dropped. Every statement from a chat
is marked as told to the assistant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pa_core.claims.records import EdgeRecord, GraphRecords, NodeRecord, stable_id
from pa_core.claims.schema import EPISODE, PARTICIPATED_IN, RECORDED_IN, SAID, STATEMENT
from pa_core.envelope import Envelope, Kind, Role
from pa_core.l0 import episode_ref
from pa_core.librarian.extraction import Extraction, quote_is_in
from pa_core.librarian.people import People
from pa_core.owner import Owner

# Whose words an episode is: an email's sender, the author of a note or a chat, or the
# organizer of a calendar event.
_SPEAKER_ROLES = frozenset({Role.SENDER, Role.AUTHOR, Role.ORGANIZER})


@dataclass
class StatementTally:
    extracted: int = 0
    kept: int = 0
    dropped_quote_not_found: int = 0


@dataclass
class Assembled:
    records: GraphRecords
    statements: StatementTally = field(default_factory=StatementTally)


def assemble(
    episodes: Sequence[Envelope],
    extractions: Mapping[str, Extraction],
    owner: Owner,
    quotable: Mapping[str, Sequence[str]],
    *,
    said_at: Mapping[str, datetime] | None = None,
) -> Assembled:
    """Records for `episodes` (in time order) and the statements extracted from each.

    A statement is kept only if its quote is in one of its episode's `quotable` texts. It was
    made when its episode happened, unless `said_at` says otherwise for that episode (a version
    of a calendar event is said when it was made, not when the event is).
    """
    said_at = said_at or {}
    people = People(owner)
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    tally = StatementTally()
    participations: set[str] = set()
    for envelope in episodes:
        eid = envelope.episode_id
        l0_path = episode_ref(envelope)
        nodes.append(_episode(envelope, l0_path))
        sender: str | None = None
        for participant in envelope.participants:
            person = people.by_address(participant.identifier, participant.name, eid)
            if participant.role in _SPEAKER_ROLES and sender is None:
                sender = person
            edge_id = stable_id("participated_in", person, eid, participant.role)
            if edge_id in participations:  # the same address twice in one header
                continue
            participations.add(edge_id)
            edges.append(
                EdgeRecord(
                    label=PARTICIPATED_IN.label,
                    id=edge_id,
                    source=person,
                    target=eid,
                    properties={"role": str(participant.role)},
                    episode_ids=(eid,),
                )
            )
        extraction = extractions.get(eid)
        if extraction is None:
            continue
        for position, statement in enumerate(extraction.statements):
            tally.extracted += 1
            if sender is None:
                continue
            if not any(quote_is_in(statement.quote, text) for text in quotable[eid]):
                tally.dropped_quote_not_found += 1
                continue
            tally.kept += 1
            sid = statement_id(eid, position)
            nodes.append(
                NodeRecord(
                    label=STATEMENT.label,
                    id=sid,
                    properties={
                        "kind": str(statement.kind),
                        "summary": statement.summary,
                        "quote": statement.quote,
                        "at": said_at.get(eid, envelope.occurred_at.start),
                        "secondhand": False,
                        "told_to_assistant": envelope.kind == Kind.CHAT,
                        "episode_id": eid,
                        "l0_path": l0_path,
                    },
                    episode_ids=(eid,),
                )
            )
            edges.append(_edge(SAID.label, sender, sid, eid))
            edges.append(_edge(RECORDED_IN.label, sid, eid, eid))
    records = GraphRecords(nodes=[*people.records(), *nodes], edges=edges)
    return Assembled(records=records, statements=tally)


def statement_id(episode_id: str, position: int) -> str:
    """A statement's id: its episode and its position in that episode's extraction, counted
    before any statement is dropped."""
    return stable_id("statement", episode_id, str(position))


def _episode(envelope: Envelope, l0_path: str) -> NodeRecord:
    return NodeRecord(
        label=EPISODE.label,
        id=envelope.episode_id,
        properties={
            "source": str(envelope.source),
            "kind": str(envelope.kind),
            "occurred_at": envelope.occurred_at.start,
            "thread_ref": envelope.thread_ref,
            "subject": envelope.subject,
            "l0_path": l0_path,
        },
        episode_ids=(envelope.episode_id,),
    )


def _edge(label: str, source: str, target: str, episode_id: str) -> EdgeRecord:
    return EdgeRecord(
        label=label,
        id=stable_id(label.lower(), source, target),
        source=source,
        target=target,
        properties={},
        episode_ids=(episode_id,),
    )
