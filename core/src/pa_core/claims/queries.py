"""The domain query interface: what a claims graph answers, and the graph-shaped results.

A storage adapter implements `ClaimsGraph` in its engine. The PA MCP server (`tools.py` and
`server.py`) puts names, messages, and name resolution around it, so an adapter only runs
queries and shapes their rows.

To add a domain query: add a method to `ClaimsGraph` returning a `Graph`, implement it in each
adapter, and add a tool for it in `ClaimsTools` and `create_server`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    type: str
    properties: dict[str, Any]
    episode_ids: list[str]


class GraphEdge(BaseModel):
    id: str
    type: str
    source: str = Field(description="The id of the node the edge comes from.")
    target: str = Field(description="The id of the node the edge goes to.")
    properties: dict[str, Any]
    episode_ids: list[str]


class Graph(BaseModel):
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    def node(self, node_id: str) -> GraphNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)


type MatchedOn = Literal["id", "address", "name", "alias", "full_text"]


class Match(BaseModel):
    id: str = Field(description="The matching node's id; its node is in `nodes`.")
    type: str
    matched_on: MatchedOn
    score: float | None = Field(default=None, description="Full-text score; higher is better.")


class Found(BaseModel):
    """What `find` returns from an adapter: ranked matches, best first, and their nodes."""

    matches: list[Match] = []
    graph: Graph = Graph()


class Rows(BaseModel):
    """What `query` returns from an adapter: its rows, and every node and edge in them."""

    columns: list[str]
    rows: list[dict[str, Any]] = Field(
        description="One object per row, by column. A node or edge appears as its id; it is in "
        "`nodes` or `edges`. A path appears as {nodes: [ids], edges: [ids]}."
    )
    truncated: bool = Field(description="True when there were more rows than the row cap.")
    graph: Graph = Graph()


class ClaimsGraph(Protocol):
    """The domain queries over the current build. Times are aware datetimes."""

    def find(self, name: str) -> Found:
        """Nodes whose id, address, name, or alias is exactly `name` (any case), ranked by
        kind of match. Only when there are none, full-text matches, best first."""
        ...

    def statements(self, who: str | None, since: datetime | None, until: datetime | None) -> Graph:
        """Statements said by the node with id `who` (anyone's when None), from `since`
        (inclusive) up to `until` (exclusive), in time order, with their speakers and episodes."""
        ...

    def query(self, text: str, parameters: Mapping[str, Any]) -> Rows:
        """One read-only statement in the engine's query language, run under the query
        limits. Raises a `PaError` saying why when it is rejected or stopped."""
        ...
