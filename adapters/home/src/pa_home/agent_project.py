"""The agent project, and rendering it for one instance.

`agent/` at the repository root is the Claude Code project the assistant runs in: CLAUDE.md,
.mcp.json (the PA MCP server), .claude/settings.json (permissions, hooks and which MCP servers
may run), .claude/hooks/ (the exchange capture hook) and .claude/skills/. It is shared by both
instances and names none of them. CLAUDE.md, .mcp.json and settings.json are templates with
`${name}` placeholders; rendering writes a filled-in copy with the owner and the instance paths.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Collection
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from pa_core.errors import PaError
from pa_core.owner import Owner

AGENT_PROJECT = Path(__file__).resolve().parents[4] / "agent"
"""`agent/` at the repository root: the shared agent project."""

_MCP_CONFIG = ".mcp.json"
"""Declares the PA MCP server, reading the instance's graph directory."""
PA_SERVER = "pa"
"""The PA MCP server's name in `.mcp.json`; its tools are `mcp__pa__<tool>` in a session."""

_SETTINGS = ".claude/settings.json"
_TEMPLATES = ("CLAUDE.md", _SETTINGS, _MCP_CONFIG)
"""The files that hold placeholders; every other file is copied as is."""


class AgentProjectError(PaError):
    """The agent project could not be rendered."""


def render_agent_project(
    target: Path,
    *,
    owner: Owner,
    l0: Path,
    scratch: Path,
    graph: Path | None = None,
    time_zone: ZoneInfo | None = None,
    hidden_tools: Collection[str] = (),
    source: Path = AGENT_PROJECT,
    python: Path = Path(sys.executable),
) -> None:
    """Writes a copy of the agent project to `target`, filled in for this instance.

    `l0` is the store the assistant reads, and `scratch` the only directory it may write to.
    `graph` is the claims graph's directory, which the PA MCP server reads; with none, the
    project declares no MCP server and the assistant has its files alone. `time_zone` is the
    instance's, which the server reads dates without an offset in; it is needed with `graph`.
    `hidden_tools` names
    PA MCP server tools the assistant is not given: settings.json denies each one by name, which
    removes it from the session while the server and its other tools stay.
    `python` runs the project's hooks; the default, the interpreter rendering it, has the core
    package installed. `target` must not exist yet, or be empty: a rendered project is replaced
    by deleting it and rendering again, never by writing over files.
    """
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise AgentProjectError(
            f"{target} is not empty. Render the agent project into a new or empty directory "
            "(delete an old rendered copy first)."
        )
    if graph is not None and time_zone is None:
        raise AgentProjectError(
            "The PA MCP server needs the instance's time zone to read dates in."
        )
    if hidden_tools and graph is None:
        raise AgentProjectError(
            "Only the PA MCP server's tools can be hidden, and with no graph there is no server."
        )
    values = {
        "owner_name": owner.name,
        "owner_other_names": ", ".join(owner.other_names) or "(none)",
        "owner_email_addresses": ", ".join(f"`{a}`" for a in owner.email_addresses),
        "project": str(target.absolute()),
        "l0": str(l0),
        "scratch": str(scratch),
        "python": str(python),
    }
    shutil.copytree(source, target, dirs_exist_ok=True)
    if graph is None:
        (target / _MCP_CONFIG).unlink()
    else:
        values["graph"] = str(graph)
        values["time_zone"] = str(time_zone)
    for name in _TEMPLATES:
        path = target / name
        if not path.exists():
            continue
        if path.suffix == ".json":
            # Each value lands inside a JSON string, so it is escaped as one.
            filled = {key: json.dumps(value)[1:-1] for key, value in values.items()}
        else:
            filled = values
        path.write_text(Template(path.read_text()).substitute(filled))
    if hidden_tools:
        _deny(target / _SETTINGS, [f"mcp__{PA_SERVER}__{tool}" for tool in sorted(hidden_tools)])


def _deny(settings_path: Path, rules: list[str]) -> None:
    settings = json.loads(settings_path.read_text())
    settings["permissions"]["deny"] = [*settings["permissions"].get("deny", []), *rules]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
