"""The agent project, and rendering it for one instance.

`agent/` at the repository root is the Claude Code project the assistant runs in: CLAUDE.md,
.claude/settings.json (permissions and hooks) and .claude/hooks/ (the exchange capture
hook). It is shared by both instances and names none of them. CLAUDE.md and settings.json are
templates with `${name}` placeholders; rendering writes a filled-in copy with the owner and the
instance paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pa_core.owner import Owner
from pa_home.project import REPOSITORY, ProjectRenderError, render_project

AGENT_PROJECT = REPOSITORY / "agent"
"""`agent/` at the repository root: the shared agent project."""

_TEMPLATES = ("CLAUDE.md", ".claude/settings.json")
"""The files that hold placeholders; every other file is copied as is."""

AgentProjectError = ProjectRenderError
"""The agent project could not be rendered."""


def render_agent_project(
    target: Path,
    *,
    owner: Owner,
    l0: Path,
    scratch: Path,
    source: Path = AGENT_PROJECT,
    python: Path = Path(sys.executable),
) -> None:
    """Writes a copy of the agent project to `target`, filled in for this instance.

    `l0` is the store the assistant reads, and `scratch` the only directory it may write to.
    `python` runs the project's hooks; the default, the interpreter rendering it, has the core
    package installed. `target` must not exist yet, or be empty: a rendered project is replaced
    by deleting it and rendering again, never by writing over files.
    """
    render_project(
        source,
        target,
        templates=_TEMPLATES,
        values={
            "owner_name": owner.name,
            "owner_other_names": ", ".join(owner.other_names) or "(none)",
            "owner_email_addresses": ", ".join(f"`{a}`" for a in owner.email_addresses),
            "project": str(target.absolute()),
            "l0": str(l0),
            "scratch": str(scratch),
            "python": str(python),
        },
        name="the agent project",
    )
