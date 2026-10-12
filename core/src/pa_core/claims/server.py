"""The PA MCP server: the claims graph's domain queries as MCP tools.

A storage adapter supplies the `ClaimsGraph` and runs the server over stdio.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from pa_core.claims.queries import ClaimsGraph
from pa_core.claims.tools import ClaimsTools, ToolResult

INSTRUCTIONS = """\
The claims graph: who said what, and when, derived from the episodes in L0 by the librarian.
It is an index; the episodes are the evidence. Every node and edge carries the ids of the
episodes it came from, and statements carry their episode's L0 path, so cite and open them.
Use `find` before concluding there is no record of someone."""

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def create_server(graph: ClaimsGraph) -> MCPServer:
    tools = ClaimsTools(graph)
    server = MCPServer("pa", instructions=INSTRUCTIONS)

    def find(name: str) -> ToolResult:
        """Find the people whose name, alias, email address, or id matches `name`.

        Exact matches (any case) come first; only if there are none, full-text matches, best
        first. When nothing matches, the message says so: that is not proof there is no
        record, so try another spelling, a nickname, or an address.
        """
        return tools.find(name)

    def statements(
        who: str | None = None, since: str | None = None, until: str | None = None
    ) -> ToolResult:
        """What someone said: their statements in time order, oldest first.

        `who` is a person's id, or a name, alias, or address, resolved as `find` does; if it
        matches more than one person, the candidates come back instead, to call again with one
        id. Leave it out for everyone's statements. `since` and `until` are ISO 8601 dates or
        datetimes (UTC unless an offset is given); `since` is inclusive, `until` is exclusive,
        and a date alone for `until` includes that whole day. Each statement comes with its
        speaker (SAID), the episode it is recorded in (RECORDED_IN), its kind, summary, and a
        quote checked against the episode's body.
        """
        return tools.statements(who, since, until)

    server.add_tool(find, annotations=_READ_ONLY)
    server.add_tool(statements, annotations=_READ_ONLY)
    return server
