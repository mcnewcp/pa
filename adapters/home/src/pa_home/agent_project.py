"""The agent project, and rendering it for one instance.

`agent/` at the repository root is the Claude Code project the assistant runs in: CLAUDE.md,
.claude/settings.json (permissions and hooks) and .claude/hooks/ (the exchange capture
hook). It is shared by both instances and names none of them. CLAUDE.md and settings.json are
templates with `${name}` placeholders; rendering writes a filled-in copy with the owner and the
instance paths.

Given a wiki, the rendered CLAUDE.md gains the section in CLAUDE.wiki.md, which says how to find
the way with it, the project gains the wiki schema shared with the librarian, and the settings
attach the wiki as an additional directory. Without one, the rendered project is the one the
assistant ran in before the wiki: L0 and nothing else.

Given a status file, CLAUDE.md gains the section in CLAUDE.status.md, which says to read it and
scope what the assistant says by it, and the settings attach the file's directory. With a wiki
too, it gains CLAUDE.wiki-status.md as well: L0 is searched directly past the time the wiki is
current through.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from string import Template

from pa_core.owner import Owner
from pa_home.librarian_project import WIKI_SCHEMA
from pa_home.project import REPOSITORY, SETTINGS, ProjectRenderError, render_project

AGENT_PROJECT = REPOSITORY / "agent"
"""`agent/` at the repository root: the shared agent project."""

_TEMPLATES = ("CLAUDE.md", SETTINGS)
"""The files that hold placeholders; every other file is copied as is."""

WIKI_SECTION = "CLAUDE.wiki.md"
"""The wiki section of CLAUDE.md, a template appended to it only when a wiki is given."""
STATUS_SECTION = "CLAUDE.status.md"
"""The status section of CLAUDE.md, a template appended to it only when a status file is
given."""
WIKI_STATUS_SECTION = "CLAUDE.wiki-status.md"
"""What the status says of the wiki, appended to CLAUDE.md only when both are given."""
SECTIONS = (WIKI_SECTION, STATUS_SECTION, WIKI_STATUS_SECTION)
"""The optional sections of CLAUDE.md. They are left out of the copy."""

AgentProjectError = ProjectRenderError
"""The agent project could not be rendered."""


def render_agent_project(
    target: Path,
    *,
    owner: Owner,
    l0: Path,
    scratch: Path,
    wiki: Path | None = None,
    status: Path | None = None,
    source: Path = AGENT_PROJECT,
    python: Path = Path(sys.executable),
) -> None:
    """Writes a copy of the agent project to `target`, filled in for this instance.

    `l0` is the store the assistant reads, and `scratch` the only directory it may write to.
    `wiki`, if given, is the wiki it reads beside L0, and `status` the status file that says
    how current they are, which it reads in its own read-only directory.
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
        exclude=list(SECTIONS),
        name="the agent project",
    )
    if wiki is not None:
        _add_section(target, source / WIKI_SECTION, directory=wiki, wiki=str(wiki))
        shutil.copyfile(WIKI_SCHEMA, target / WIKI_SCHEMA.name)
    if status is not None:
        _add_section(target, source / STATUS_SECTION, directory=status.parent, status=str(status))
        if wiki is not None:
            _add_section(target, source / WIKI_STATUS_SECTION)


def _add_section(
    target: Path, section: Path, *, directory: Path | None = None, **values: str
) -> None:
    """Appends `section`, filled in with `values`, to the rendered project's CLAUDE.md, and
    attaches `directory`, if given, to its settings."""
    claude_md = target / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + Template(section.read_text()).substitute(values))
    if directory is None:
        return
    settings_path = target / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["permissions"]["additionalDirectories"].append(str(directory))
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
