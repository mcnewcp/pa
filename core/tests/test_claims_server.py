"""The PA MCP server's size budget, through an in-process MCP client over a fake claims graph
big enough to need it: what the agent gets back from `find` and the domain tools."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import pytest
from mcp import Client

from pa_core.claims.queries import (
    Direction,
    Found,
    Graph,
    GraphEdge,
    GraphNode,
    Match,
    Rows,
    Schema,
)
from pa_core.claims.results import BUDGET
from pa_core.claims.server import create_server

START = datetime(2026, 4, 1, 14, tzinfo=UTC)
WORDY = "the adjuster said the mold supplement is approved and the drywall goes up next week " * 2


def node(node_id: str, node_type: str, episode_ids: list[str], **properties: Any) -> GraphNode:
    return GraphNode(id=node_id, type=node_type, properties=properties, episode_ids=episode_ids)


def edge(edge_type: str, source: str, target: str, episode: str, **properties: Any) -> GraphEdge:
    return GraphEdge(
        id=f"{edge_type.lower()}_{source}_{target}",
        type=edge_type,
        source=source,
        target=target,
        properties=properties,
        episode_ids=[episode],
    )


def at(index: int) -> str:
    return (START + timedelta(hours=7 * index)).isoformat()


@dataclass
class BigGraph:
    """A `ClaimsGraph` holding one big topic: `count` statements, calendar events, and open
    requests, a few hours apart, each with its speaker or participants and its episode."""

    count: int

    def find(self, name: str) -> Found:
        if name == "topic_basement":
            topic = node(name, "Topic", [], name="Basement flood", aliases=[], description="")
            return Found(
                matches=[Match(id=name, type="Topic", matched_on="id")],
                graph=Graph(nodes=[topic]),
            )
        people = [
            node(f"person_{i:04d}", "Person", [f"gmail_{i:04d}"], name=f"Sam {i}", aliases=[])
            for i in range(self.count)
        ]
        matches = [Match(id=p.id, type="Person", matched_on="full_text") for p in people]
        return Found(matches=matches, graph=Graph(nodes=people))

    def statements(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        for i in self._within(since, until):
            episode = f"gmail_{i:04d}"
            statement = f"statement_{i:04d}"
            speaker = f"person_{i % 7}"
            nodes.setdefault(speaker, node(speaker, "Person", [episode], name=f"Person {i % 7}"))
            nodes[statement] = node(
                statement,
                "Statement",
                [episode],
                kind="reported_status",
                summary=f"Statement {i}: {WORDY}",
                quote=f"Quote {i}: {WORDY}",
                at=at(i),
                secondhand=False,
                told_to_assistant=False,
                episode_id=episode,
                l0_path=f"episodes/gmail/2026-04/{episode}.json",
            )
            nodes[episode] = node(
                episode, "Episode", [episode], source="gmail", kind="email", occurred_at=at(i)
            )
            edges += [
                edge("SAID", speaker, statement, episode),
                edge("RECORDED_IN", statement, episode, episode),
            ]
        return Graph(nodes=list(nodes.values()), edges=edges)

    def timeline(self, topic: str, since: datetime | None, until: datetime | None) -> Graph:
        return self.statements(None, topic, since, until)

    def events(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        for i in self._within(since, until):
            episode = f"icloud_calendar_{i:04d}"
            event = f"calendar_event_{i:04d}"
            fields = {"title": f"Visit {i}: {WORDY}", "starts_at": at(i), "status": "scheduled"}
            nodes[event] = node(event, "CalendarEvent", [episode], **fields)
            nodes[episode] = node(episode, "Episode", [episode], kind="event")
            edges.append(edge("VERSION_OF", episode, event, episode, **fields))
            for person in (f"person_{i % 7}", "person_owner"):
                nodes.setdefault(person, node(person, "Person", [episode], name=person))
                edges.append(edge("PARTICIPATED_IN", person, episode, episode, role="attendee"))
        return Graph(nodes=list(nodes.values()), edges=edges)

    def open_loops(self, who: str | None, direction: Direction | None) -> Graph:
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        for i in range(self.count):
            episode, request, statement = f"gmail_{i:04d}", f"request_{i:04d}", f"statement_{i}"
            nodes[request] = node(
                request, "Request", [episode], what=WORDY, due=None, status="open", opened_at=at(i)
            )
            nodes.setdefault("person_owner", node("person_owner", "Person", [], name="Owner"))
            nodes[statement] = node(
                statement, "Statement", [episode], quote=WORDY, at=at(i), episode_id=episode
            )
            nodes[episode] = node(episode, "Episode", [episode], kind="email")
            edges += [
                edge("OWES", "person_owner", request, episode),
                edge("OPENS", statement, request, episode),
                edge("RECORDED_IN", statement, episode, episode),
            ]
        return Graph(nodes=list(nodes.values()), edges=edges)

    def query(self, text: str, parameters: Mapping[str, Any]) -> Rows:
        raise NotImplementedError

    def schema(self) -> Schema:
        raise NotImplementedError

    def _within(self, since: datetime | None, until: datetime | None) -> list[int]:
        return [
            i
            for i in range(self.count)
            if (since is None or datetime.fromisoformat(at(i)) >= since)
            and (until is None or datetime.fromisoformat(at(i)) < until)
        ]


def call(graph: BigGraph, tool: str, **arguments: Any) -> tuple[dict[str, Any], str]:
    """A tool's structured result, and the text the client gets beside it."""

    async def go() -> tuple[dict[str, Any], str]:
        server = create_server(graph, time_zone=ZoneInfo("America/Chicago"))
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments)
        assert not result.is_error, result.content
        assert result.structured_content is not None
        (text,) = result.content
        return result.structured_content, getattr(text, "text", "")

    return anyio.run(go)


def ids(result: dict[str, Any], node_type: str) -> list[str]:
    return [n["id"] for n in result["nodes"] if n["type"] == node_type]


def test_the_text_beside_a_result_is_the_result_as_compact_json():
    result, text = call(BigGraph(3), "statements")

    assert json.loads(text) == result
    assert text == json.dumps(result, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize(
    ("tool", "anchor", "arguments"),
    [
        ("statements", "Statement", {}),
        ("timeline", "Statement", {"topic": "topic_basement"}),
        ("events", "CalendarEvent", {}),
    ],
)
def test_a_result_over_the_budget_is_cut_at_a_time_and_says_where_to_pick_up(
    tool: str, anchor: str, arguments: dict[str, str]
):
    graph = BigGraph(400)

    first, text = call(graph, tool, **arguments)

    assert len(text) <= BUDGET
    kept = ids(first, anchor)
    assert 0 < len(kept) < 400
    assert kept == sorted(kept)  # the oldest first
    assert "400" in first["message"]
    assert f"first {len(kept)}" in first["message"]
    (since,) = re.findall(r"since=(\S+?)\.?(?:\s|$)", first["message"])
    assert since == at(len(kept))  # where the first item left out starts
    rest, _ = call(graph, tool, **arguments, since=since)
    assert ids(rest, anchor)[0] == f"{kept[0][: -len('0000')]}{len(kept):04d}"


def test_every_item_in_a_cut_result_comes_whole():
    first, _ = call(BigGraph(400), "statements")

    node_ids = {n["id"] for n in first["nodes"]}
    statements = [n for n in first["nodes"] if n["type"] == "Statement"]
    said = {e["target"] for e in first["edges"] if e["type"] == "SAID"}
    for statement in statements:
        assert statement["id"] in said
        assert statement["properties"]["episode_id"] in node_ids
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in first["edges"])


def test_open_loops_over_the_budget_say_to_narrow_by_who_and_direction():
    result, text = call(BigGraph(400), "open_loops")

    assert len(text) <= BUDGET
    kept = ids(result, "Request")
    assert 0 < len(kept) < 400
    assert f"first {len(kept)}" in result["message"]
    assert "`who`" in result["message"] and "`direction`" in result["message"]


def test_find_over_the_budget_keeps_the_best_matches_and_says_to_be_more_specific():
    result, text = call(BigGraph(1000), "find", name="sam")

    assert len(text) <= BUDGET
    kept = [match["id"] for match in result["matches"]]
    assert 0 < len(kept) < 1000
    assert kept == ids(result, "Person") == [f"person_{i:04d}" for i in range(len(kept))]
    assert f"first {len(kept)}" in result["message"]
    assert "address" in result["message"]


def test_a_result_within_the_budget_is_whole_and_says_nothing_of_cutting():
    result, _ = call(BigGraph(20), "timeline", topic="topic_basement")

    assert len(ids(result, "Statement")) == 20
    assert result["message"] == "20 statements about Basement flood (topic_basement), oldest first."
