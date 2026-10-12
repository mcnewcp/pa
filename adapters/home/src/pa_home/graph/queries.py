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
MATCH (n:Person:Organization:Topic)
WITH n, CASE
    WHEN n.id = $key THEN 'id'
    WHEN list_contains(n.addresses, $key) THEN 'address'
    WHEN list_contains(n.domains, $key) THEN 'domain'
    WHEN lower(n.name) = $key THEN 'name'
    WHEN list_contains(list_transform(n.aliases, a -> lower(a)), $key) THEN 'alias'
  END AS matched_on
WHERE matched_on IS NOT NULL
RETURN n AS node, matched_on, NULL AS score
ORDER BY CASE matched_on WHEN 'id' THEN 0 WHEN 'address' THEN 1 WHEN 'domain' THEN 1
    WHEN 'name' THEN 2 ELSE 3 END,
  n.name, n.id""",
    rules=(
        "$key is the name in lower case with its whitespace collapsed. A person, "
        "organization, or topic matches when it is exactly its id, one of its addresses "
        "(people and organizations), one of an organization's mail domains, its name, or one "
        "of its aliases, ignoring case. Runs first: full-text search tokenizes away digits "
        "and punctuation, so it can't be trusted to rank an exact address or alias first."
    ),
)


def _find_full_text(name: str, label: str, index: str, holds: str) -> DomainQuery:
    return DomainQuery(
        name=name,
        cypher=f"""\
CALL QUERY_FTS_INDEX('{label}', '{index}', $text)
WITH node, score
WHERE score > 0
RETURN node, 'full_text' AS matched_on, score
ORDER BY score DESC, node.id
LIMIT 10""",
        rules=(
            "Runs only when nothing matched exactly, beside the other find_*full_text queries, "
            f"whose hits are merged by score (at most 10, best first). Searches each "
            f"{label}'s `{SEARCH_TEXT}` ({holds} together), with English stemming and "
            "stopwords; any word may match. Hits scoring 0 are dropped: a word found in every "
            f"{label} scores 0 and would return them all."
        ),
    )


FIND_FULL_TEXT = (
    _find_full_text("find_full_text", "Person", "person_fts", "name, aliases, and addresses"),
    _find_full_text(
        "find_organizations_full_text",
        "Organization",
        "organization_fts",
        "name, aliases, domains, and addresses",
    ),
    _find_full_text(
        "find_topics_full_text", "Topic", "topic_fts", "name, aliases, and description"
    ),
)
"""One query per full-text index: a union can't join nodes of different types, and an index
with no hits would end a query that chained them."""

MEMBERSHIPS = DomainQuery(
    name="memberships",
    cypher="""\
MATCH (p:Person)-[m:MEMBER_OF]->(o:Organization)
WHERE list_contains($ids, p.id) OR list_contains($ids, o.id)
RETURN p, m, o
ORDER BY o.name, p.name, m.id""",
    rules=(
        "Runs after find, for its matches: the organizations each matched person belongs to, "
        "and the people each matched organization has, so a name comes with who it is "
        "connected to. A person belongs to an organization by writing from its mail domain "
        "or by an episode saying so."
    ),
)

STATEMENTS = DomainQuery(
    name="statements",
    cypher="""\
MATCH (p)-[said:SAID]->(s:Statement)-[rec:RECORDED_IN]->(e:Episode)
WHERE (CAST($who AS STRING) IS NULL OR p.id = $who)
  AND (CAST($topic AS STRING) IS NULL
    OR EXISTS { MATCH (s)-[:ABOUT]->(about_topic:Topic) WHERE about_topic.id = $topic })
  AND (CAST($since AS TIMESTAMP) IS NULL OR s.at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR s.at < CAST($until AS TIMESTAMP))
OPTIONAL MATCH (s)-[about:ABOUT]->(t:Topic)
OPTIONAL MATCH (s)-[m:MENTIONS]->(x)
RETURN p, said, s, rec, e, about, t, m, x
ORDER BY s.at, s.id""",
    rules=(
        "Statements said by the person or organization $who (everyone's when null) about the "
        "topic $topic (about anything or nothing when null), from $since (inclusive) to "
        "$until (exclusive), in UTC, oldest first. Each comes with its speaker (SAID), the "
        "episode it is recorded in (RECORDED_IN), every topic it is about (ABOUT), and the "
        "people and organizations it mentions (MENTIONS). A secondhand statement is one the "
        "episode's sender recounts, so its speaker didn't write that episode. Statements are "
        "events and are never invalidated, so a later one may contradict an earlier one: read "
        "them in order."
    ),
)

TIMELINE_STATEMENTS = DomainQuery(
    name="timeline_statements",
    cypher="""\
MATCH (t:Topic)<-[about:ABOUT]-(s:Statement)-[rec:RECORDED_IN]->(e:Episode),
  (speaker)-[said:SAID]->(s)
WHERE t.id = $topic
  AND (CAST($since AS TIMESTAMP) IS NULL OR s.at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR s.at < CAST($until AS TIMESTAMP))
RETURN s.at AS at, t, about, s, speaker, said, rec, e
ORDER BY s.at, s.id""",
    rules=(
        "The timeline's statements: each statement about the topic $topic (ABOUT) from "
        "$since (inclusive) to $until (exclusive), in UTC, oldest first, with its speaker "
        "(SAID) and its episode (RECORDED_IN). Topics are flat, so there are no subtopics "
        "to follow."
    ),
)

EVENTS = DomainQuery(
    name="events",
    cypher="""\
MATCH (e:Episode)-[v:VERSION_OF]->(c:CalendarEvent)
WHERE (CAST($who AS STRING) IS NULL
    OR EXISTS { MATCH (attendee)-[:PARTICIPATED_IN]->(:Episode)-[:VERSION_OF]->(c)
      WHERE attendee.id = $who })
  AND (CAST($topic AS STRING) IS NULL
    OR EXISTS { MATCH (c)-[:ABOUT]->(about_topic:Topic) WHERE about_topic.id = $topic })
  AND (CAST($since AS TIMESTAMP) IS NULL OR c.starts_at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR c.starts_at < CAST($until AS TIMESTAMP))
OPTIONAL MATCH (p)-[part:PARTICIPATED_IN]->(e)
OPTIONAL MATCH (c)-[about:ABOUT]->(t:Topic)
RETURN c, v, e, p, part, about, t
ORDER BY c.starts_at, c.id, v.changed_at, e.id""",
    rules=(
        "Calendar events that $who takes part in (as organizer or attendee of any version; "
        "anyone's when null), about the topic $topic (any when null), whose current start is "
        "from $since (inclusive) to $until (exclusive), in UTC, by start time. Cancelled "
        "events are included, with status 'cancelled'. A CalendarEvent holds its current "
        "version; every version comes as a VERSION_OF edge from its episode, carrying that "
        "version's fields and when it was made (changed_at), with the version's participants "
        "(PARTICIPATED_IN, with their role) and the event's topics (ABOUT)."
    ),
)

TIMELINE_EVENTS = DomainQuery(
    name="timeline_events",
    cypher="""\
MATCH (t:Topic)<-[about:ABOUT]-(c:CalendarEvent)<-[v:VERSION_OF]-(e:Episode)
WHERE t.id = $topic
  AND (CAST($since AS TIMESTAMP) IS NULL OR c.starts_at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR c.starts_at < CAST($until AS TIMESTAMP))
RETURN c.starts_at AS at, t, about, c, v, e
ORDER BY c.starts_at, c.id, v.changed_at, e.id""",
    rules=(
        "The timeline's calendar events: each event about the topic $topic (ABOUT) whose "
        "current start is from $since (inclusive) to $until (exclusive), in UTC, placed at "
        "that start, with every version (VERSION_OF, from its episode). Use events for who "
        "takes part."
    ),
)

TIMELINE: tuple[DomainQuery, ...] = (TIMELINE_STATEMENTS, TIMELINE_EVENTS)
"""The parts of `timeline`. Each returns an `at` column, and their rows are merged in `at`
order, so calendar events and loop openings and closings join it as parts of their own."""

DOMAIN_QUERIES: tuple[DomainQuery, ...] = (
    FIND_EXACT,
    *FIND_FULL_TEXT,
    MEMBERSHIPS,
    STATEMENTS,
    *TIMELINE,
    EVENTS,
)


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
            hits = [
                row for q in FIND_FULL_TEXT for row in self._runner.run(q.cypher, {"text": key})
            ]
            rows = sorted(hits, key=lambda row: (-row["score"], row["node"]["id"]))[:10]
        ids = sorted({row["node"]["id"] for row in rows})
        memberships = self._runner.run(MEMBERSHIPS.cypher, {"ids": ids}) if ids else []
        shaped = shape([*rows, *memberships])
        matches = [
            Match(
                id=row["node"],
                type=shaped.graph.nodes_by_id[row["node"]].type,
                matched_on=row["matched_on"],
                score=row["score"],
            )
            for row in shaped.rows[: len(rows)]
        ]
        return Found(matches=matches, graph=shaped.graph.as_graph())

    def statements(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        rows = self._runner.run(
            STATEMENTS.cypher,
            {"who": who, "topic": topic, **_span(since, until)},
        )
        return shape(rows).graph.as_graph()

    def timeline(self, topic: str, since: datetime | None, until: datetime | None) -> Graph:
        rows: list[Row] = []
        for query in TIMELINE:
            rows += self._runner.run(query.cypher, {"topic": topic, **_span(since, until)})
        rows.sort(key=lambda row: row["at"])  # stable, so each part's own order breaks ties
        return shape(rows).graph.as_graph()

    def events(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        rows = self._runner.run(EVENTS.cypher, {"who": who, "topic": topic, **_span(since, until)})
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
    properties, and one matched under several labels at once, as in
    `(n:Person:Organization:Topic)`, the other labels' columns; those come back null, so the
    declaration decides which are its own."""
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


def _span(since: datetime | None, until: datetime | None) -> dict[str, datetime | None]:
    return {"since": _engine_time(since), "until": _engine_time(until)}


def _engine_time(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.astimezone(UTC).replace(tzinfo=None)
