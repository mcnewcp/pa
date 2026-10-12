"""The configurations an eval run can ask: what the agent is given besides its files.

Every configuration keeps file access to L0. One that has the claims graph builds it for each
run from the run's own L0 (see `pa_evals.librarian`) and gives the agent the PA MCP server over
it, with all of the server's tools or only some.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from pa_core.claims.server import tool_names


@dataclass(frozen=True)
class Configuration:
    name: str
    about: str
    """What it gives the agent, for `pa-eval run --help`."""
    graph: bool = False
    """Whether the run builds the claims graph and the agent gets the PA MCP server."""
    tools: AbstractSet[str] | None = None
    """The PA MCP server's tools the agent gets, by name; None for all of them."""

    def hidden_tools(self) -> frozenset[str]:
        """The PA MCP server's tools this configuration keeps from the agent."""
        if self.tools is None:
            return frozenset()
        return frozenset(tool_names()) - self.tools


FILES_ALONE = Configuration(
    "files-alone", "file access to L0 and no MCP server (the v0.11 configuration)"
)
GRAPH_PLUS_FILES = Configuration(
    "graph-plus-files", "the claims graph with all of the PA MCP server's tools, plus files", True
)
QUERY_ONLY = Configuration(
    "query-only",
    "the claims graph's find, schema, and query tools without the domain queries, plus files",
    True,
    frozenset({"find", "schema", "query"}),
)

CONFIGURATIONS: dict[str, Configuration] = {
    configuration.name: configuration
    for configuration in (FILES_ALONE, GRAPH_PLUS_FILES, QUERY_ONLY)
}
"""The configurations a run can ask, by name."""
DEFAULT_CONFIGURATION = FILES_ALONE
