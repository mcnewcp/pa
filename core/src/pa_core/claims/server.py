"""The PA MCP server: the claims graph's domain queries as MCP tools.

A storage adapter supplies the `ClaimsGraph` and runs the server over stdio.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from pa_core.claims.queries import ClaimsGraph, Schema
from pa_core.claims.tools import ClaimsTools, QueryResult, ToolResult
from pa_core.errors import PaError

INSTRUCTIONS = """\
The claims graph: who said what, about which topic, and when, derived from the episodes in L0
by the librarian. It is an index; the episodes are the evidence. Every node and edge carries the
ids of the episodes it came from, and statements carry their episode's L0 path, so cite and
open them. Topics are the matters the owner asks about by name (a kitchen remodel, a wedding);
`timeline` tells what happened about one. Use `find` before concluding there is no record of
someone or something."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def create_server(graph: ClaimsGraph) -> MCPServer:
    tools = ClaimsTools(graph)
    server = MCPServer("pa", instructions=INSTRUCTIONS)

    def find(name: str) -> ToolResult:
        """Find the people, organizations, and topics whose name, alias, email address, mail
        domain, or id matches `name`.

        Exact matches (any case) come first; only if there are none, full-text matches, best
        first. Each match comes with its memberships (MEMBER_OF): the organizations a person
        belongs to, or an organization's people. When nothing matches, the message says so:
        that is not proof there is no record, so try another spelling, a nickname, or an
        address.
        """
        with _reported():
            return tools.find(name)

    def statements(
        who: str | None = None,
        topic: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> ToolResult:
        """What someone said, optionally about one topic: statements in time order, oldest
        first.

        `who` is a person's or organization's id, or a name, alias, or address; `topic` is a
        topic's id or name. Each is resolved as `find` does, and if it matches more than one,
        the candidates come back instead, to call again with one id. Leave `who` out for
        everyone's statements and `topic` out for statements about anything. `since` and
        `until` are ISO 8601 dates or datetimes (UTC unless an offset is given); `since` is
        inclusive, `until` is exclusive, and a date alone for `until` includes that whole day.
        Each statement comes with its speaker (SAID), the episode it is recorded in
        (RECORDED_IN), the topics it is about (ABOUT), whom it mentions (MENTIONS), its kind,
        summary, and a quote checked against the episode's body. A secondhand statement is one
        the episode's sender recounts ("Maria told me…"): say so when you cite it. Only the
        owner's own episodes hold the owner's statements; anyone else's claim about the owner
        is that person's report, mentioning the owner.
        """
        with _reported():
            return tools.statements(who, topic, since, until)

    def timeline(topic: str, since: str | None = None, until: str | None = None) -> ToolResult:
        """What has happened about a topic, in time order, oldest first: "what's happened with
        the kitchen remodel?" in one call.

        `topic` is a topic's id or name, resolved as `find` does; if it matches more than one
        topic, the candidates come back instead, to call again with one id. `since` and
        `until` work as in `statements`. Each statement about the topic comes with its speaker
        (SAID), its episode (RECORDED_IN), and the topic (ABOUT).
        """
        with _reported():
            return tools.timeline(topic, since, until)

    def schema() -> Schema:
        """The claims graph's live schema, for writing a `query`: its tables with their
        properties, the relationships between them, and the full-text indexes. Also every
        domain query's own Cypher and rules, to adapt rather than starting from nothing, the
        limits `query` runs under, and quirks of the query language.
        """
        with _reported():
            return tools.schema()

    def query(cypher: str, params: dict[str, Any] | None = None) -> QueryResult:
        """Run one read-only Cypher statement, for questions no other tool answers (paths,
        connections, counts). Prefer a domain query when one fits.

        Call `schema` first: it gives the tables, the indexes, every domain query's own Cypher
        to adapt, and this tool's limits. Pass values as `params` and refer to them as `$name`.
        Only read clauses and a few read-only procedures are accepted; anything else is
        rejected before it reaches the database. A query that runs too long or uses too much
        memory is stopped. Rows are capped, and `truncated` says when some were cut off. Return
        a relationship together with both of its nodes, or return the whole path.
        """
        with _reported():
            return tools.query(cypher, params)

    server.add_tool(find, annotations=_READ_ONLY)
    server.add_tool(statements, annotations=_READ_ONLY)
    server.add_tool(timeline, annotations=_READ_ONLY)
    server.add_tool(schema, annotations=_READ_ONLY)
    server.add_tool(query, annotations=_READ_ONLY)
    return server


@contextmanager
def _reported() -> Iterator[None]:
    """Our own errors (no build yet, a malformed date) reach the assistant with their message;
    anything else is a crash, which the SDK reports without its details."""
    try:
        yield
    except PaError as error:
        raise ToolError(str(error)) from error
