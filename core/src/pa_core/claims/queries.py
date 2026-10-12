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


type Direction = Literal["owed_by", "owed_to"]
"""For open loops: those someone owes, or those owed to them."""

type MatchedOn = Literal["id", "address", "domain", "name", "alias", "full_text"]


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


class Property(BaseModel):
    name: str
    type: str


class Connection(BaseModel):
    source: str = Field(description="The table an edge of this type comes from.")
    target: str = Field(description="The table it goes to.")


class Table(BaseModel):
    name: str
    kind: str = Field(description="NODE or REL, in the engine's words.")
    properties: list[Property]
    connections: list[Connection] = Field(
        default=[], description="For a relationship table, the node tables it joins."
    )


class Index(BaseModel):
    table: str
    name: str
    type: str
    properties: list[str]


class DomainQueryText(BaseModel):
    name: str
    text: str = Field(description="The query exactly as the domain tool runs it.")
    rules: str = Field(description="What it includes and excludes, and why.")
    parameters: list[str] = Field(description="The parameters it takes, as `$name` in `text`.")


class QueryLimits(BaseModel):
    max_rows: int | None = Field(description="Rows past this are cut off, and flagged.")
    timeout_seconds: float
    memory_mb: int = Field(description="A query using more is stopped.")
    starts: list[str] = Field(description="The clauses a statement may start with.")
    procedures: list[str] = Field(description="The only procedures CALL may run.")
    rejected: list[str] = Field(description="Keywords that get a statement rejected.")


class Schema(BaseModel):
    """What `schema` returns: the current build's live schema, the domain queries' own text
    to adapt for `query`, and `query`'s limits."""

    language: str
    tables: list[Table]
    indexes: list[Index]
    domain_queries: list[DomainQueryText]
    query_limits: QueryLimits
    notes: list[str] = Field(description="Quirks of the query language worth knowing.")


class ClaimsGraph(Protocol):
    """The domain queries over the current build. Times are aware datetimes."""

    def find(self, name: str) -> Found:
        """People, organizations, and topics whose id, address, mail domain, name, or alias is
        exactly `name` (any case), ranked by kind of match. Only when there are none, full-text
        matches, best first. The graph also holds the matches' memberships (MEMBER_OF)."""
        ...

    def statements(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        """Statements said by the person or organization with id `who` (anyone's when None)
        about the topic with id `topic` (any when None), from `since` (inclusive) up to `until`
        (exclusive), in time order, with their speakers, episodes, topics, and mentions."""
        ...

    def timeline(self, topic: str, since: datetime | None, until: datetime | None) -> Graph:
        """What happened about the topic with id `topic`, from `since` (inclusive) up to
        `until` (exclusive), in time order: its statements, with their speakers and
        episodes, and its calendar events at their current start, with their versions."""
        ...

    def events(
        self,
        who: str | None,
        topic: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Graph:
        """Calendar events that the node with id `who` takes part in (anyone's when None),
        about the topic with id `topic` (any when None), whose current start is from `since`
        (inclusive) up to `until` (exclusive), by start time: each in its current version, with
        every version (VERSION_OF, from its episode), each version's participants, and its
        topics."""
        ...

    def open_loops(self, who: str | None, direction: Direction | None) -> Graph:
        """The requests and commitments still open that the person or organization with id
        `who` owes (`owed_by`), is owed (`owed_to`), or either (None); everyone's when `who`
        is None. Oldest first, each with who owes it (OWES), whom it is owed to (OWED_TO), the
        statement that opened it (OPENS) and that statement's episode, and the request it
        was accepted from or the promise it superseded."""
        ...

    def query(self, text: str, parameters: Mapping[str, Any]) -> Rows:
        """One read-only statement in the engine's query language, run under the query
        limits. Raises a `PaError` saying why when it is rejected or stopped."""
        ...

    def schema(self) -> Schema:
        """The current build's live schema, every domain query's text and rules, and the
        `query` limits."""
        ...
