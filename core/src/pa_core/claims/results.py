"""What `find` and the domain tools give the assistant: a `Graph` in a compact encoding.

The result is still graph-shaped, nodes and edges, but leaves out what the assistant doesn't
use, so a big topic's timeline fits in one tool result:

- An edge has a type, its two ends, and its properties only when it has some. It has no id.
- A node or edge carries `episode_ids` only when they say more than the nodes do: a statement
  names its episode (`episode_id`), and an episode is itself, so neither repeats it, and
  neither does an edge whose episodes one of its ends already names.
- A statement's episode is a node in the result, holding its L0 path, so the statement names
  the episode by id alone, and no RECORDED_IN edge repeats that.
- When the call is limited to one topic, everything in the result is about it, so no ABOUT
  edge to that topic is given. (A timeline's statements that only open or close one of the
  topic's requests or commitments are part of its story too.)
- A property that is false is left out (`secondhand`, `told_to_assistant`, `is_owner`).
- A person, organization, or topic is given by its id and name alone, unless the call is about
  it: find's matches, and the `who` or `topic` the call names. Their `episode_ids` are never
  given: they list every episode the node appears in, which the domain tools narrow down.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from pa_core.claims.queries import Graph, GraphEdge, GraphNode

BUDGET = 30_000
"""The most characters a result of `find` or a domain tool may take, as compact JSON.

Claude Code hands the assistant a tool result over 50,000 characters, or over 25,000 tokens,
as a file to dig through instead, and warns over 10,000 tokens. With their hex ids, results run
at about 2 to 3 characters a token, so this is about 10,000 to 15,000 tokens."""

ENTITY_TYPES = frozenset({"Person", "Organization", "Topic"})
"""Who and what the graph is about. Unless the call is about one, it is given by name."""

_IMPLIED_EDGES = frozenset({"RECORDED_IN"})
"""Edges a node's properties already state: a statement's `episode_id`."""


def _empty(value: Any) -> bool:
    return not value


class ResultNode(BaseModel):
    id: str
    type: str
    properties: dict[str, Any]
    episode_ids: list[str] = Field(
        default=[],
        exclude_if=_empty,
        description="The episodes it came from, when its properties don't already say so.",
    )


class ResultEdge(BaseModel):
    type: str
    source: str = Field(description="The id of the node the edge comes from.")
    target: str = Field(description="The id of the node the edge goes to.")
    properties: dict[str, Any] = Field(default={}, exclude_if=_empty)
    episode_ids: list[str] = Field(
        default=[],
        exclude_if=_empty,
        description="The episodes it came from, when neither of its nodes already says so.",
    )


class CompactGraph(BaseModel):
    nodes: list[ResultNode] = []
    edges: list[ResultEdge] = []


def compact(graph: Graph, subjects: Collection[str] = (), topic: str | None = None) -> CompactGraph:
    """`graph` in the compact encoding. `subjects` are the ids of the nodes the call is
    about, which keep all their properties. `topic` is the id of the topic the call is limited
    to, if any: everything in the result is about it, so no ABOUT edge to it is given."""
    encoder = _Encoder(graph, subjects, topic)
    return CompactGraph(
        nodes=[encoder.node(node) for node in graph.nodes],
        edges=[encoded for edge in graph.edges if (encoded := encoder.edge(edge))],
    )


def fit(
    graph: Graph,
    anchors: Sequence[str],
    room: int,
    subjects: Collection[str] = (),
    topic: str | None = None,
    beside: Mapping[str, int] | None = None,
) -> tuple[CompactGraph, int]:
    """As much of `graph` as fits in `room` characters of compact JSON, in the compact
    encoding, and how many of `anchors` it holds: all of it when it fits, or else the items of
    the first anchors, in order, as many as fit.

    An item is an anchor node (a statement, a calendar event, an open loop, a match) and what
    comes with it: the nodes reached from it along edges, never through another anchor or
    through a person, organization, or topic, nor back from an episode to its other
    statements. Edges come along once both their nodes have. `beside` is how many characters
    each anchor takes outside the graph, as a match does."""
    beside = beside or {}
    encoder = _Encoder(graph, subjects, topic)
    whole = compact(graph, subjects, topic)
    if _length(whole) + sum(beside.values()) <= room:
        return whole, len(anchors)
    lengths = {node.id: _length(encoder.node(node)) + 1 for node in graph.nodes}
    incident: dict[str, list[tuple[int, GraphEdge]]] = defaultdict(list)
    for index, edge in enumerate(graph.edges):
        incident[edge.source].append((index, edge))
        incident[edge.target].append((index, edge))
    kept: set[str] = set()
    used = _length(CompactGraph())
    count = 0
    for anchor, item in zip(anchors, _items(graph, anchors, incident), strict=True):
        new = item - kept
        joined = kept | new
        edges = {
            index: edge
            for node_id in new
            for index, edge in incident[node_id]
            if edge.source in joined and edge.target in joined
        }
        cost = (
            beside.get(anchor, 0)
            + sum(lengths[node_id] for node_id in new)
            + sum(
                _length(encoded) + 1 for edge in edges.values() if (encoded := encoder.edge(edge))
            )
        )
        if used + cost > room:
            break
        kept, used, count = joined, used + cost, count + 1
    part = Graph(
        nodes=[node for node in graph.nodes if node.id in kept],
        edges=[edge for edge in graph.edges if edge.source in kept and edge.target in kept],
    )
    return compact(part, subjects, topic), count


def _items(
    graph: Graph, anchors: Sequence[str], incident: Mapping[str, list[tuple[int, GraphEdge]]]
) -> Iterator[set[str]]:
    types = {node.id: node.type for node in graph.nodes}
    barriers = set(anchors)
    for anchor in anchors:
        item, reached = {anchor}, [anchor]
        while reached:
            here = reached.pop()
            if here != anchor and types.get(here) in ENTITY_TYPES:
                continue
            for _, edge in incident.get(here, []):
                if types.get(here) == "Episode" and edge.type == "RECORDED_IN":
                    continue
                there = edge.target if edge.source == here else edge.source
                if there not in item and there not in barriers and there in types:
                    item.add(there)
                    reached.append(there)
        yield item


def _length(model: BaseModel) -> int:
    return len(model.model_dump_json())


class _Encoder:
    """Each node and edge of `graph` in the compact encoding, on its own."""

    def __init__(self, graph: Graph, subjects: Collection[str], topic: str | None) -> None:
        self._present = {node.id for node in graph.nodes}
        self._said = {node.id: _episodes_said(node) for node in graph.nodes}
        self._subjects = set(subjects)
        self._topic = topic

    def node(self, node: GraphNode) -> ResultNode:
        properties = {key: value for key, value in node.properties.items() if value is not False}
        if node.type in ENTITY_TYPES and node.id not in self._subjects:
            properties = {"name": node.properties.get("name")}
        if node.type == "Statement" and properties.get("episode_id") in self._present:
            properties.pop("l0_path", None)
        return ResultNode(
            id=node.id,
            type=node.type,
            properties=properties,
            episode_ids=_shown_episode_ids(node),
        )

    def edge(self, edge: GraphEdge) -> ResultEdge | None:
        """The edge, or None when the result says what it says some other way."""
        if edge.type in _IMPLIED_EDGES or (edge.type == "ABOUT" and edge.target == self._topic):
            return None
        named = self._said.get(edge.source, set()) | self._said.get(edge.target, set())
        return ResultEdge(
            type=edge.type,
            source=edge.source,
            target=edge.target,
            properties=edge.properties,
            episode_ids=[] if set(edge.episode_ids) <= named else edge.episode_ids,
        )


def _own_episodes(node: GraphNode) -> set[str]:
    """The episodes a node names by its own id or properties."""
    if node.type == "Episode":
        return {node.id}
    if node.type == "Statement":
        return {node.properties["episode_id"]}
    return set()


def _shown_episode_ids(node: GraphNode) -> list[str]:
    if node.type in ENTITY_TYPES or set(node.episode_ids) <= _own_episodes(node):
        return []
    return node.episode_ids


def _episodes_said(node: GraphNode) -> set[str]:
    """The episodes the node names in the result."""
    return _own_episodes(node) | set(_shown_episode_ids(node))
