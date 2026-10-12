"""The domain queries in Cypher, and the `ClaimsGraph` that runs them on LadybugDB.

Each domain query is a Cypher string and a short rules note saying what it includes and
excludes, and why. Code only binds parameters and shapes rows; what a query returns is decided
by its Cypher alone, which is what `schema()` shows the assistant.

To add a domain query: define a `DomainQuery`, add it to `DOMAIN_QUERIES`, and add the method
that binds and shapes it to `LadybugClaimsGraph`. Queries return nodes and relationships with
both endpoints in the same row, so `shape` can give every edge its endpoints' stable ids.
`schema()` lists every query in `DOMAIN_QUERIES`, and a test runs each one through the `query`
tool, so a domain query must also pass its allowlist (`pa_home.graph.guard`).

The `query` tool's open-ended Cypher is checked against that allowlist here, before it is
sent to the query worker, and shaped the same way, with paths unpacked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pa_core.claims.queries import (
    DomainQueryText,
    Found,
    Graph,
    GraphEdge,
    GraphNode,
    Match,
    QueryLimits,
    Rows,
    Schema,
)
from pa_core.claims.schema import EDGE_TYPES, NODE_TYPES
from pa_core.errors import PaError
from pa_home.graph import guard
from pa_home.graph.live_schema import DIALECT_NOTES, LANGUAGE, live_indexes, live_tables
from pa_home.graph.reader import CypherRunner, QueryRunner, Row
from pa_home.graph.store import SEARCH_TEXT


@dataclass(frozen=True)
class DomainQuery:
    name: str
    cypher: str
    rules: str

    @property
    def parameters(self) -> list[str]:
        """Its `$name` parameters, in order of first use."""
        return list(dict.fromkeys(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", self.cypher)))


FIND_EXACT = DomainQuery(
    name="find_exact",
    cypher="""\
MATCH (p:Person)
WITH p, CASE
    WHEN p.id = $key THEN 'id'
    WHEN list_contains(p.addresses, $key) THEN 'address'
    WHEN lower(p.name) = $key THEN 'name'
    WHEN list_contains(list_transform(p.aliases, a -> lower(a)), $key) THEN 'alias'
  END AS matched_on
WHERE matched_on IS NOT NULL
RETURN p AS node, matched_on, NULL AS score
ORDER BY CASE matched_on WHEN 'id' THEN 0 WHEN 'address' THEN 1 WHEN 'name' THEN 2 ELSE 3 END,
  p.name, p.id""",
    rules=(
        "$key is the name in lower case with its whitespace collapsed. A person matches when "
        "it is exactly their id, one of their addresses, their name, or one of their aliases, "
        "ignoring case. Runs first: full-text search tokenizes away digits and punctuation, "
        "so it can't be trusted to rank an exact address or alias first."
    ),
)

FIND_FULL_TEXT = DomainQuery(
    name="find_full_text",
    cypher="""\
CALL QUERY_FTS_INDEX('Person', 'person_fts', $text)
WITH node, score
WHERE score > 0
RETURN node, 'full_text' AS matched_on, score
ORDER BY score DESC, node.id
LIMIT 10""",
    rules=(
        f"Runs only when nothing matched exactly. Searches each person's `{SEARCH_TEXT}` "
        "(name, aliases, and addresses together), with English stemming and stopwords; any "
        "word may match. Hits scoring 0 are dropped: a word found in every person scores 0 "
        "and would return everyone. At most 10, best first."
    ),
)

STATEMENTS = DomainQuery(
    name="statements",
    cypher="""\
MATCH (p:Person)-[said:SAID]->(s:Statement)-[rec:RECORDED_IN]->(e:Episode)
WHERE (CAST($who AS STRING) IS NULL OR p.id = $who)
  AND (CAST($since AS TIMESTAMP) IS NULL OR s.at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR s.at < CAST($until AS TIMESTAMP))
RETURN p, said, s, rec, e
ORDER BY s.at, s.id""",
    rules=(
        "Statements said by the person $who (everyone's when null), from $since (inclusive) "
        "to $until (exclusive), in UTC, oldest first. Each comes with its speaker (SAID) and "
        "the episode it is recorded in (RECORDED_IN). Statements are events and are never "
        "invalidated, so a later one may contradict an earlier one: read them in order."
    ),
)

DOMAIN_QUERIES: tuple[DomainQuery, ...] = (FIND_EXACT, FIND_FULL_TEXT, STATEMENTS)


class LadybugClaimsGraph:
    """`ClaimsGraph` over LadybugDB: the domain queries run through `runner`, and the `query`
    tool's open-ended queries, once allowlisted, through `query_runner` under its limits."""

    def __init__(self, runner: CypherRunner, *, query_runner: QueryRunner) -> None:
        self._runner = runner
        self._query_runner = query_runner

    def find(self, name: str) -> Found:
        key = " ".join(name.split()).lower()
        if not key:
            return Found()
        rows = self._runner.run(FIND_EXACT.cypher, {"key": key})
        if not rows and any(character.isalpha() for character in key):
            rows = self._runner.run(FIND_FULL_TEXT.cypher, {"text": key})
        shaped = shape(rows)
        matches = [
            Match(
                id=row["node"],
                type=shaped.graph.nodes_by_id[row["node"]].type,
                matched_on=row["matched_on"],
                score=row["score"],
            )
            for row in shaped.rows
        ]
        return Found(matches=matches, graph=shaped.graph.as_graph())

    def statements(self, who: str | None, since: datetime | None, until: datetime | None) -> Graph:
        rows = self._runner.run(
            STATEMENTS.cypher,
            {"who": who, "since": _engine_time(since), "until": _engine_time(until)},
        )
        return shape(rows).graph.as_graph()

    def schema(self) -> Schema:
        limits = self._query_runner.limits
        return Schema(
            language=LANGUAGE,
            tables=live_tables(self._runner),
            indexes=live_indexes(self._runner),
            domain_queries=[
                DomainQueryText(
                    name=query.name,
                    text=query.cypher,
                    rules=query.rules,
                    parameters=query.parameters,
                )
                for query in DOMAIN_QUERIES
            ],
            query_limits=QueryLimits(
                max_rows=limits.max_rows,
                timeout_seconds=limits.timeout_seconds,
                memory_mb=min(limits.memory_bytes, limits.buffer_pool_bytes) >> 20,
                starts=list(guard.READ_CLAUSES),
                procedures=list(guard.PROCEDURES),
                rejected=list(guard.REJECTED),
            ),
            notes=list(DIALECT_NOTES),
        )

    def query(self, text: str, parameters: Mapping[str, Any]) -> Rows:
        guard.check_read_only(text)
        fetched = self._query_runner.fetch(text, parameters)
        shaped = shape(fetched.rows)
        return Rows(
            columns=fetched.columns,
            rows=shaped.rows,
            truncated=fetched.truncated,
            graph=shaped.graph.as_graph(),
        )


class UnshapeableRows(PaError):
    """Rows that can't be shaped into a graph."""


@dataclass
class _ShapedGraph:
    nodes_by_id: dict[str, GraphNode]
    edges_by_id: dict[str, GraphEdge]

    def as_graph(self) -> Graph:
        return Graph(nodes=list(self.nodes_by_id.values()), edges=list(self.edges_by_id.values()))


@dataclass
class Shaped:
    graph: _ShapedGraph
    rows: list[dict[str, Any]]
    """The rows with each node or relationship replaced by its stable id."""


def shape(rows: list[Row]) -> Shaped:
    """Every node and relationship in `rows` as a graph with stable ids, in first-seen order.

    Nodes and relationships are found anywhere in a row: in a column, in a list, or in a path.
    In the shaped rows each is replaced by its stable id, and a path by `{"nodes": [ids],
    "edges": [ids]}`. LadybugDB's internal ids are used only to find each relationship's
    endpoints among the nodes in the same result, and never leave this function.
    """
    graph = _ShapedGraph({}, {})
    internal: dict[tuple[int, int], str] = {}
    for row in rows:
        for value in row.values():
            _collect_nodes(value, graph, internal)
    shaped_rows = [
        {column: _shape_value(value, graph, internal) for column, value in row.items()}
        for row in rows
    ]
    return Shaped(graph=graph, rows=shaped_rows)


def _collect_nodes(value: Any, graph: _ShapedGraph, internal: dict[tuple[int, int], str]) -> None:
    if _is_node(value):
        node = _node(value)
        graph.nodes_by_id.setdefault(node.id, node)
        internal[_internal_id(value["_ID"])] = node.id
    elif isinstance(value, dict):
        for item in value.values():
            _collect_nodes(item, graph, internal)
    elif isinstance(value, list):
        for item in value:
            _collect_nodes(item, graph, internal)


def _shape_value(value: Any, graph: _ShapedGraph, internal: dict[tuple[int, int], str]) -> Any:
    if _is_node(value):
        return value["id"]
    if _is_relationship(value):
        edge = _edge(value, internal)
        graph.edges_by_id.setdefault(edge.id, edge)
        return edge.id
    if _is_path(value):
        return {
            "nodes": [_shape_value(node, graph, internal) for node in value["_NODES"]],
            "edges": [_shape_value(rel, graph, internal) for rel in value["_RELS"]],
        }
    if isinstance(value, dict):
        return {key: _shape_value(item, graph, internal) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape_value(item, graph, internal) for item in value]
    return _plain(value)


def _is_node(value: Any) -> bool:
    return isinstance(value, dict) and "_LABEL" in value and "_ID" in value and "_SRC" not in value


def _is_relationship(value: Any) -> bool:
    return isinstance(value, dict) and "_SRC" in value and "_DST" in value


def _is_path(value: Any) -> bool:
    return isinstance(value, dict) and "_NODES" in value and "_RELS" in value


def _internal_id(value: dict[str, int]) -> tuple[int, int]:
    return (value["table"], value["offset"])


_DECLARED: dict[str, frozenset[str]] = {
    declared.label: frozenset(declared.properties) for declared in (*NODE_TYPES, *EDGE_TYPES)
}


def _properties(value: dict[str, Any]) -> dict[str, Any]:
    """The properties its type declares. A node in a path carries every node table's
    properties, the others null, so the declaration decides which are its own."""
    declared = _DECLARED.get(value["_LABEL"])
    hidden = {"id", "episode_ids", SEARCH_TEXT}
    return {
        key: _plain(item)
        for key, item in value.items()
        if not key.startswith("_") and key not in hidden and (declared is None or key in declared)
    }


def _node(value: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=value["id"],
        type=value["_LABEL"],
        properties=_properties(value),
        episode_ids=list(value.get("episode_ids") or []),
    )


def _edge(value: dict[str, Any], internal: dict[tuple[int, int], str]) -> GraphEdge:
    try:
        source = internal[_internal_id(value["_SRC"])]
        target = internal[_internal_id(value["_DST"])]
    except KeyError:
        raise UnshapeableRows(
            f"A {value['_LABEL']} relationship came back without both of its nodes, so its "
            "ends have no ids: return each relationship with both of its nodes, or the whole "
            "path."
        ) from None
    return GraphEdge(
        id=value["id"],
        type=value["_LABEL"],
        source=source,
        target=target,
        properties=_properties(value),
        episode_ids=list(value.get("episode_ids") or []),
    )


def _plain(value: Any) -> Any:
    """A JSON-ready value; timestamps, stored in UTC without an offset, get theirs back."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.isoformat()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _engine_time(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.astimezone(UTC).replace(tzinfo=None)
