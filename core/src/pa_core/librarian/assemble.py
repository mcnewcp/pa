"""Assembly: turning episodes and their extractions into the graph's node and edge records.

This is where who said what is decided:

- A firsthand statement's speaker is the episode's sender, author (the owner, for a daily
  note or a chat), or organizer (for a calendar event), from its metadata, whatever the model
  wrote. A secondhand statement's speaker is the person or organization the model named,
  resolved; the statement is secondhand only if that is someone other than the sender or
  author.
- A statement is the owner's only if the episode is the owner's own (their sent mail, their
  note, their turns in a chat, an event they organized). In anyone else's episode, a
  statement that resolves to the owner is recorded as the sender's report (kind: reported
  status) mentioning the owner, so no one can speak for the owner.
- A statement whose quote is not in the episode's quotable texts (for a chat, one of the
  owner's turns; for a calendar event, its title and description) is dropped. Every statement
  from a chat is marked as told to the assistant.

People and organizations are resolved in the order episodes are offered (time order).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pa_core.claims.records import EdgeRecord, GraphRecords, NodeRecord, stable_id
from pa_core.claims.schema import (
    EPISODE,
    MENTIONS,
    PARTICIPATED_IN,
    RECORDED_IN,
    SAID,
    STATEMENT,
)
from pa_core.envelope import Envelope, Kind, Role
from pa_core.l0 import episode_ref
from pa_core.librarian.extraction import (
    ExtractedStatement,
    Extraction,
    MentionType,
    StatementKind,
    quote_is_in,
    render_header,
)
from pa_core.librarian.people import Identify, People
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
    identify: Identify,
    *,
    said_at: Mapping[str, datetime] | None = None,
) -> Assembled:
    """Records for `episodes` (in time order) and the statements extracted from each.

    A statement is kept only if its quote is in one of its episode's `quotable` texts.
    `identify` is asked who a name is when the records can't decide. A statement was made when
    its episode happened, unless `said_at` says otherwise for that episode (a version of a
    calendar event is said when it was made, not when the event is).
    """
    said_at = said_at or {}
    people = People(owner, identify)
    nodes: list[NodeRecord] = []
    edges: list[EdgeRecord] = []
    tally = StatementTally()
    participations: set[str] = set()
    for envelope in episodes:
        eid = envelope.episode_id
        l0_path = episode_ref(envelope)
        header = render_header(envelope)
        extraction = extractions.get(eid)
        nodes.append(_episode(envelope, l0_path))
        sender: str | None = None
        for participant in envelope.participants:
            is_sender = participant.role in _SPEAKER_ROLES and sender is None
            party = people.participant(
                participant.identifier,
                participant.name,
                eid,
                f"{header}\n\nIt appears as the episode's {participant.role}, "
                f"at {participant.identifier}.",
                as_organization=bool(
                    is_sender and extraction and extraction.sender_is_organization
                ),
                organization=extraction.sender_organization if is_sender and extraction else None,
            )
            if is_sender:
                sender = party
            edge_id = stable_id("participated_in", party, eid, participant.role)
            if edge_id in participations:  # the same address twice in one header
                continue
            participations.add(edge_id)
            edges.append(
                EdgeRecord(
                    label=PARTICIPATED_IN.label,
                    id=edge_id,
                    source=party,
                    target=eid,
                    properties={"role": str(participant.role)},
                    episode_ids=(eid,),
                )
            )
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
            context = f"{header}\n\nIt is named in this statement: {statement.summary}"
            speaker, kind, mentioned = _attribute(statement, sender, people, eid, context)
            for party in (speaker, *mentioned):
                people.known_from(party, statement.summary)
            sid = statement_id(eid, position)
            nodes.append(
                NodeRecord(
                    label=STATEMENT.label,
                    id=sid,
                    properties={
                        "kind": str(kind),
                        "summary": statement.summary,
                        "quote": statement.quote,
                        "at": said_at.get(eid, envelope.occurred_at.start),
                        "secondhand": speaker != sender,
                        "told_to_assistant": envelope.kind == Kind.CHAT,
                        "episode_id": eid,
                        "l0_path": l0_path,
                    },
                    episode_ids=(eid,),
                )
            )
            edges.append(_edge(SAID.label, speaker, sid, eid))
            edges.append(_edge(RECORDED_IN.label, sid, eid, eid))
            edges.extend(_edge(MENTIONS.label, sid, party, eid) for party in mentioned)
    party_nodes, memberships = people.records()
    records = GraphRecords(nodes=[*party_nodes, *nodes], edges=[*edges, *memberships])
    return Assembled(records=records, statements=tally)


def statement_id(episode_id: str, position: int) -> str:
    """A statement's id: its episode and its position in that episode's extraction, counted
    before any statement is dropped."""
    return stable_id("statement", episode_id, str(position))


def _attribute(
    statement: ExtractedStatement, sender: str, people: People, episode_id: str, context: str
) -> tuple[str, StatementKind, list[str]]:
    """The statement's speaker, its kind, and who it mentions (each once, never the speaker)."""
    speaker = sender
    if statement.secondhand:
        speaker = people.speaker(statement.speaker, episode_id, context) or sender
    mentioned: list[str] = []
    for mention in statement.mentions:
        party_kind = "organization" if mention.type == MentionType.ORGANIZATION else "person"
        party = people.mention(mention.name, party_kind, episode_id, context)
        if party is None:
            continue
        mentioned.append(party)
        if mention.organization:
            people.join(party, mention.organization, episode_id, context)
    kind: StatementKind = statement.kind
    if speaker == people.owner_id and sender != people.owner_id:
        # Only the owner's own episodes speak for the owner.
        speaker, kind = sender, StatementKind.REPORTED_STATUS
        mentioned.append(people.owner_id)
    return speaker, kind, [party for party in dict.fromkeys(mentioned) if party != speaker]


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
