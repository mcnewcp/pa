"""The claims graph's schema, engine-neutral: node and edge types and their properties.

A storage adapter turns this into its engine's tables. Every node and every edge also has an
`id` (derived only from its inputs, so it is stable across rebuilds) and `episode_ids` (the
episodes it came from); those two are not listed in `properties`.

To add a type: declare it here and add it to `NODE_TYPES` or `EDGE_TYPES`. The librarian then
produces records of it, and the store creates, loads, and counts it with no further change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class PropertyType(StrEnum):
    STRING = "string"
    STRINGS = "strings"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    """An aware datetime. Engines may store it in UTC; it reads back in UTC."""
    DATE = "date"
    """A calendar day, with no time or zone."""


@dataclass(frozen=True)
class NodeType:
    label: str
    properties: Mapping[str, PropertyType]


@dataclass(frozen=True)
class EdgeType:
    label: str
    endpoints: tuple[tuple[str, str], ...]
    """The (from, to) node labels this edge may join."""
    properties: Mapping[str, PropertyType]


_S, _SS, _B, _T, _D = (
    PropertyType.STRING,
    PropertyType.STRINGS,
    PropertyType.BOOL,
    PropertyType.TIMESTAMP,
    PropertyType.DATE,
)

PERSON = NodeType("Person", {"name": _S, "aliases": _SS, "addresses": _SS, "is_owner": _B})
ORGANIZATION = NodeType(
    "Organization", {"name": _S, "aliases": _SS, "domains": _SS, "addresses": _SS}
)
"""`addresses` are the organization's own (an appointments desk), where it writes as itself."""
EPISODE = NodeType(
    "Episode",
    {
        "source": _S,
        "kind": _S,
        "occurred_at": _T,
        "thread_ref": _S,
        "subject": _S,
        "l0_path": _S,
    },
)
STATEMENT = NodeType(
    "Statement",
    {
        "kind": _S,
        "summary": _S,
        "quote": _S,
        "at": _T,
        "secondhand": _B,
        "told_to_assistant": _B,
        "episode_id": _S,
        "l0_path": _S,
    },
)
TOPIC = NodeType("Topic", {"name": _S, "aliases": _SS, "description": _S})

_LOOP = {"what": _S, "due": _D, "status": _S, "opened_at": _T}
REQUEST = NodeType("Request", _LOOP)
"""Someone asking someone to do something. `status` is `open`, or how it closed: `fulfilled`,
`declined`, `withdrawn`, or `accepted` (it became a commitment, ACCEPTED_AS). Never overdue:
that depends on when you ask."""
COMMITMENT = NodeType("Commitment", _LOOP)
"""A promise. `status` is `open`, `fulfilled`, `withdrawn`, or `superseded` (by a newer
promise, which SUPERSEDES it)."""
_LOOPS = ("Request", "Commitment")
_PARTIES = ("Person", "Organization")

SAID = EdgeType("SAID", (("Person", "Statement"), ("Organization", "Statement")), {})
RECORDED_IN = EdgeType("RECORDED_IN", (("Statement", "Episode"),), {})
PARTICIPATED_IN = EdgeType(
    "PARTICIPATED_IN", (("Person", "Episode"), ("Organization", "Episode")), {"role": _S}
)
ABOUT = EdgeType("ABOUT", (("Statement", "Topic"),), {})
MENTIONS = EdgeType("MENTIONS", (("Statement", "Person"), ("Statement", "Organization")), {})
MEMBER_OF = EdgeType("MEMBER_OF", (("Person", "Organization"),), {})
OPENS = EdgeType("OPENS", tuple(("Statement", loop) for loop in _LOOPS), {})
CLOSES = EdgeType("CLOSES", tuple(("Statement", loop) for loop in _LOOPS), {"how": _S})
"""`how` is the status the request or commitment closed with."""
OWES = EdgeType("OWES", tuple((party, loop) for party in _PARTIES for loop in _LOOPS), {})
"""From whoever is to do it: the one asked, or the one who promised."""
OWED_TO = EdgeType("OWED_TO", tuple((loop, party) for loop in _LOOPS for party in _PARTIES), {})
"""To whomever it is owed: the one who asked, or the one promised."""
ACCEPTED_AS = EdgeType("ACCEPTED_AS", (("Request", "Commitment"),), {})
SUPERSEDES = EdgeType("SUPERSEDES", (("Commitment", "Commitment"),), {})
"""From the newer promise to the one it replaced."""

NODE_TYPES: tuple[NodeType, ...] = (
    PERSON,
    ORGANIZATION,
    EPISODE,
    STATEMENT,
    TOPIC,
    REQUEST,
    COMMITMENT,
)
EDGE_TYPES: tuple[EdgeType, ...] = (
    SAID,
    RECORDED_IN,
    PARTICIPATED_IN,
    ABOUT,
    MENTIONS,
    MEMBER_OF,
    OPENS,
    CLOSES,
    OWES,
    OWED_TO,
    ACCEPTED_AS,
    SUPERSEDES,
)
