"""The domain queries in Cypher, and the `ClaimsGraph` that runs them on LadybugDB.

Each domain query is a Cypher string and a short rules note saying what it includes and
excludes, and why. Code only binds parameters and shapes rows; what a query returns is decided
by its Cypher alone, which is what `schema()` will show the assistant.

To add a domain query: define a `DomainQuery`, add it to `DOMAIN_QUERIES`, and add the method
that binds and shapes it to `LadybugClaimsGraph`. Queries return nodes and relationships with
both endpoints in the same row, so `shape` can give every edge its endpoints' stable ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pa_core.claims.queries import Found, Graph, GraphEdge, GraphNode, Match
from pa_core.claims.schema import EDGE_TYPES, NODE_TYPES
from pa_home.graph.reader import CypherRunner, Row
from pa_home.graph.store import SEARCH_TEXT


@dataclass(frozen=True)
class DomainQuery:
    name: str
    cypher: str
    rules: str


FIND_EXACT = DomainQuery(
    name="find_exact",
    cypher="""\
MATCH (n:Person:Topic)
WITH n, CASE
    WHEN n.id = $key THEN 'id'
    WHEN list_contains(n.addresses, $key) THEN 'address'
    WHEN lower(n.name) = $key THEN 'name'
    WHEN list_contains(list_transform(n.aliases, a -> lower(a)), $key) THEN 'alias'
  END AS matched_on
WHERE matched_on IS NOT NULL
RETURN n AS node, matched_on, NULL AS score
ORDER BY CASE matched_on WHEN 'id' THEN 0 WHEN 'address' THEN 1 WHEN 'name' THEN 2 ELSE 3 END,
  n.name, n.id""",
    rules=(
        "$key is the name in lower case with its whitespace collapsed. A person or topic "
        "matches when it is exactly its id, one of its addresses (people only), its name, or "
        "one of its aliases, ignoring case. Runs first: full-text search tokenizes away "
        "digits and punctuation, so it can't be trusted to rank an exact address or alias "
        "first."
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

FIND_TOPICS_FULL_TEXT = DomainQuery(
    name="find_topics_full_text",
    cypher="""\
CALL QUERY_FTS_INDEX('Topic', 'topic_fts', $text)
WITH node, score
WHERE score > 0
RETURN node, 'full_text' AS matched_on, score
ORDER BY score DESC, node.id
LIMIT 10""",
    rules=(
        f"Runs beside find_full_text, only when nothing matched exactly. Searches each "
        f"topic's `{SEARCH_TEXT}` (name, aliases, and description together) the same way. "
        "The hits of both are merged by score, and the best 10 kept."
    ),
)

STATEMENTS = DomainQuery(
    name="statements",
    cypher="""\
MATCH (p:Person)-[said:SAID]->(s:Statement)-[rec:RECORDED_IN]->(e:Episode)
WHERE (CAST($who AS STRING) IS NULL OR p.id = $who)
  AND (CAST($topic AS STRING) IS NULL
    OR EXISTS { MATCH (s)-[:ABOUT]->(about_topic:Topic) WHERE about_topic.id = $topic })
  AND (CAST($since AS TIMESTAMP) IS NULL OR s.at >= CAST($since AS TIMESTAMP))
  AND (CAST($until AS TIMESTAMP) IS NULL OR s.at < CAST($until AS TIMESTAMP))
OPTIONAL MATCH (s)-[about:ABOUT]->(t:Topic)
RETURN p, said, s, rec, e, about, t
ORDER BY s.at, s.id""",
    rules=(
        "Statements said by the person $who (everyone's when null) about the topic $topic "
        "(about anything or nothing when null), from $since (inclusive) to $until "
        "(exclusive), in UTC, oldest first. Each comes with its speaker (SAID), the episode "
        "it is recorded in (RECORDED_IN), and every topic it is about (ABOUT). Statements are "
        "events and are never invalidated, so a later one may contradict an earlier one: "
        "read them in order."
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

TIMELINE: tuple[DomainQuery, ...] = (TIMELINE_STATEMENTS,)
"""The parts of `timeline`. Each returns an `at` column, and their rows are merged in `at`
order, so calendar events and loop openings and closings join it as parts of their own."""

DOMAIN_QUERIES: tuple[DomainQuery, ...] = (
    FIND_EXACT,
    FIND_FULL_TEXT,
    FIND_TOPICS_FULL_TEXT,
    STATEMENTS,
    *TIMELINE,
)


class LadybugClaimsGraph:
    """`ClaimsGraph` over LadybugDB, running every query through a `CypherRunner`."""

    def __init__(self, runner: CypherRunner) -> None:
        self._runner = runner

    def find(self, name: str) -> Found:
        key = " ".join(name.split()).lower()
        if not key:
            return Found()
        rows = self._runner.run(FIND_EXACT.cypher, {"key": key})
        if not rows and any(character.isalpha() for character in key):
            for query in (FIND_FULL_TEXT, FIND_TOPICS_FULL_TEXT):
                rows += self._runner.run(query.cypher, {"text": key})
            rows = sorted(rows, key=lambda row: -row["score"])[:10]
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

    LadybugDB's internal ids are used only to find each relationship's endpoints among the
    nodes in the same result, and never leave this function.
    """
    graph = _ShapedGraph({}, {})
    internal: dict[tuple[int, int], str] = {}
    for row in rows:
        for value in row.values():
            if _is_node(value):
                node = _node(value)
                graph.nodes_by_id.setdefault(node.id, node)
                internal[_internal_id(value["_ID"])] = node.id
    shaped_rows = []
    for row in rows:
        shaped: dict[str, Any] = {}
        for column, value in row.items():
            if _is_node(value):
                shaped[column] = value["id"]
            elif _is_relationship(value):
                edge = _edge(value, internal)
                graph.edges_by_id.setdefault(edge.id, edge)
                shaped[column] = edge.id
            else:
                shaped[column] = _plain(value)
        shaped_rows.append(shaped)
    return Shaped(graph=graph, rows=shaped_rows)


def _is_node(value: Any) -> bool:
    return isinstance(value, dict) and "_LABEL" in value and "_ID" in value and "_SRC" not in value


def _is_relationship(value: Any) -> bool:
    return isinstance(value, dict) and "_SRC" in value and "_DST" in value


def _internal_id(value: dict[str, int]) -> tuple[int, int]:
    return (value["table"], value["offset"])


_DECLARED = {t.label: frozenset(t.properties) for t in (*NODE_TYPES, *EDGE_TYPES)}


def _properties(value: dict[str, Any]) -> dict[str, Any]:
    """The properties its type declares. A node matched under several labels at once, as in
    `(n:Person:Topic)`, also carries the other labels' columns as nulls; they are left out."""
    declared = _DECLARED[value["_LABEL"]]
    return {key: _plain(item) for key, item in value.items() if key in declared}


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
        raise ValueError(
            f"a {value['_LABEL']} relationship came back without both of its nodes; "
            "domain queries must return each relationship's endpoints too"
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
