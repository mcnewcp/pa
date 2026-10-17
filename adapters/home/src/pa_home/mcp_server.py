"""The PA MCP server on the home instance, reading the current build of a graph directory."""

from __future__ import annotations

from datetime import tzinfo
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from pa_core.claims.server import create_server
from pa_home.graph import extensions
from pa_home.graph.layout import GraphDirectory
from pa_home.graph.queries import LadybugClaimsGraph
from pa_home.graph.reader import Limits
from pa_home.graph.worker import DOMAIN_LIMITS, QUERY_LIMITS, GuardedWorker


def graph_server(
    graph_dir: Path,
    *,
    time_zone: tzinfo,
    extensions_root: Path | None = None,
    domain_limits: Limits = DOMAIN_LIMITS,
    query_limits: Limits = QUERY_LIMITS,
) -> MCPServer:
    """The server over whichever build `graph_dir/current` points at when each call starts.

    The domain queries and `find` run in one guarded worker process, and the `query` tool's
    queries in another, each under its own limits, so neither a runaway query nor an engine
    crash takes the server down, and a slow `query` doesn't hold up the domain queries.

    A date or time the assistant gives without a UTC offset is read in `time_zone`, the
    instance's. It never downloads: the full-text extension must already be at the pinned
    local path.
    """
    fts = extensions.verified(extensions_root or extensions.extensions_dir(), extensions.FTS)
    graph = GraphDirectory(graph_dir)
    domain = GuardedWorker(graph, fts_extension=fts, limits=domain_limits)
    open_ended = GuardedWorker(graph, fts_extension=fts, limits=query_limits)
    return create_server(LadybugClaimsGraph(domain, query_runner=open_ended), time_zone=time_zone)
