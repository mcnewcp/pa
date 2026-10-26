"""The agent project, and rendering it for one instance.

`agent/` at the repository root is the Claude Code project the assistant runs in: CLAUDE.md,
.claude/settings.json (permissions) and .claude/hooks/ (the exchange capture hook's script, which
the home VM's managed settings run, since only managed hooks run there). It is shared by both
instances and names none of them. CLAUDE.md and settings.json are
templates with `${name}` placeholders; rendering writes a filled-in copy with the owner and the
instance paths.

Given a wiki, the rendered CLAUDE.md gains the section in CLAUDE.wiki.md, which says how to find
the way with it, the project gains the wiki schema shared with the librarian, and the settings
attach the wiki as an additional directory. Without one, the rendered project is the one the
assistant ran in before the wiki: L0 and nothing else.
"""

from __future__ import annotations

import json
import shutil
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
"""The wiki section of CLAUDE.md, a template appended to it only when a wiki is given. It is
left out of the copy."""

AgentProjectError = ProjectRenderError
"""The agent project could not be rendered."""


def render_agent_project(
    target: Path,
    *,
    owner: Owner,
    l0: Path,
    scratch: Path,
    wiki: Path | None = None,
    source: Path = AGENT_PROJECT,
) -> None:
    """Writes a copy of the agent project to `target`, filled in for this instance.

    `l0` is the store the assistant reads, and `scratch` the only directory it may write to.
    `wiki`, if given, is the wiki it reads beside L0.
    `target` must not exist yet, or be empty: a rendered project is replaced
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
            "l0": str(l0),
            "scratch": str(scratch),
        },
        exclude=[WIKI_SECTION],
        name="the agent project",
    )
    if wiki is not None:
        _add_wiki(target, wiki=wiki, source=source)


def _add_wiki(target: Path, *, wiki: Path, source: Path) -> None:
    """Points the rendered project in `target` at `wiki`."""
    section = Template((source / WIKI_SECTION).read_text()).substitute(wiki=str(wiki))
    claude_md = target / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + section)
    shutil.copyfile(WIKI_SCHEMA, target / WIKI_SCHEMA.name)
    settings_path = target / SETTINGS
    settings = json.loads(settings_path.read_text())
    settings["permissions"]["additionalDirectories"].append(str(wiki))
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
