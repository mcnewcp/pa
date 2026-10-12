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


_S, _SS, _B, _T = (
    PropertyType.STRING,
    PropertyType.STRINGS,
    PropertyType.BOOL,
    PropertyType.TIMESTAMP,
)

PERSON = NodeType("Person", {"name": _S, "aliases": _SS, "addresses": _SS, "is_owner": _B})
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

SAID = EdgeType("SAID", (("Person", "Statement"),), {})
RECORDED_IN = EdgeType("RECORDED_IN", (("Statement", "Episode"),), {})
PARTICIPATED_IN = EdgeType("PARTICIPATED_IN", (("Person", "Episode"),), {"role": _S})

NODE_TYPES: tuple[NodeType, ...] = (PERSON, EPISODE, STATEMENT)
EDGE_TYPES: tuple[EdgeType, ...] = (SAID, RECORDED_IN, PARTICIPATED_IN)
