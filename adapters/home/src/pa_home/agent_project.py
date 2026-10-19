"""The agent project, and rendering it for one instance.

`agent/` at the repository root is the Claude Code project the assistant runs in: CLAUDE.md,
.claude/settings.json (permissions and hooks) and .claude/hooks/ (the exchange capture
hook). It is shared by both instances and names none of them. CLAUDE.md and settings.json are
templates with `${name}` placeholders; rendering writes a filled-in copy with the owner and the
instance paths.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from string import Template

from pa_core.errors import PaError
from pa_core.owner import Owner

AGENT_PROJECT = Path(__file__).resolve().parents[4] / "agent"
"""`agent/` at the repository root: the shared agent project."""

_TEMPLATES = ("CLAUDE.md", ".claude/settings.json")
"""The files that hold placeholders; every other file is copied as is."""


class AgentProjectError(PaError):
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
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise AgentProjectError(
            f"{target} is not empty. Render the agent project into a new or empty directory "
            "(delete an old rendered copy first)."
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
    for name in _TEMPLATES:
        path = target / name
        if path.suffix == ".json":
            # Each value lands inside a JSON string, so it is escaped as one.
            filled = {key: json.dumps(value)[1:-1] for key, value in values.items()}
        else:
            filled = values
        path.write_text(Template(path.read_text()).substitute(filled))
