"""The PA MCP server on the home instance, reading the current build of a graph directory."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from pa_core.claims.server import create_server
from pa_home.graph import extensions
from pa_home.graph.layout import GraphDirectory
from pa_home.graph.queries import LadybugClaimsGraph
from pa_home.graph.reader import CurrentBuild


def graph_server(graph_dir: Path, *, extensions_root: Path | None = None) -> MCPServer:
    """The server over whichever build `graph_dir/current` points at when each call starts.

    It never downloads: the full-text extension must already be at the pinned local path.
    """
    fts = extensions.verified(extensions_root or extensions.extensions_dir(), extensions.FTS)
    runner = CurrentBuild(GraphDirectory(graph_dir), fts_extension=fts)
    return create_server(LadybugClaimsGraph(runner))
