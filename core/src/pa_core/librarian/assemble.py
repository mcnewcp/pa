"""Assembly: turning episodes and their extractions into the graph's node and edge records.

This is where who said what is decided. A firsthand statement's speaker is the episode's
sender, from its metadata, whatever the model wrote; a statement whose quote is not in the
episode's body is dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pa_core.claims.records import EdgeRecord, GraphRecords, NodeRecord, stable_id
from pa_core.claims.schema import EPISODE, PARTICIPATED_IN, RECORDED_IN, SAID, STATEMENT
from pa_core.envelope import Envelope, Role
from pa_core.l0 import episode_ref
from pa_core.librarian.extraction import Extraction, quote_is_in
from pa_core.librarian.people import People
from pa_core.owner import Owner


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
    episodes: Sequence[Envelope], extractions: Mapping[str, Extraction], owner: Owner
) -> Assembled:
    """Records for `episodes` (in time order) and the statements extracted from each."""
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
            if participant.role == Role.SENDER and sender is None:
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
            if not quote_is_in(statement.quote, envelope.body):
                tally.dropped_quote_not_found += 1
                continue
            tally.kept += 1
            sid = stable_id("statement", eid, str(position))
            nodes.append(
                NodeRecord(
                    label=STATEMENT.label,
                    id=sid,
                    properties={
                        "kind": str(statement.kind),
                        "summary": statement.summary,
                        "quote": statement.quote,
                        "at": envelope.occurred_at.start,
                        "secondhand": False,
                        "told_to_assistant": False,
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
