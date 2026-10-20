"""The librarian project, and rendering it for one instance.

`librarian/` at the repository root is the Claude Code project the librarian's sessions run in:
its instructions (CLAUDE.md), the wiki schema (wiki-schema.md), the page and day-log templates
(templates/), and its settings (.claude/settings.json). It is shared by both instances and
names none of them. Rendering fills in the owner and the paths the session sees L0 and the wiki
at; the schema and templates are copied as is. The settings allow only the file tools and
attach L0 and the wiki as additional directories; they reach
the session as flag settings, since `claude -p` never trusts a project folder (and the wiki is
never attached with `--add-dir`, which would load skills from it).
"""

from __future__ import annotations

from pathlib import Path, PurePath

from pa_core.owner import Owner
from pa_home.project import REPOSITORY, render_project

LIBRARIAN_PROJECT = REPOSITORY / "librarian"
"""`librarian/` at the repository root: the shared librarian project."""

SETTINGS = ".claude/settings.json"
"""The project's settings, relative to the project."""

WIKI_SCHEMA = LIBRARIAN_PROJECT / "wiki-schema.md"
"""The wiki schema: the wiki's layout, page types, and line formats, written for every reader
of the wiki (the librarian, and the assistant). It holds no instance values."""

_TEMPLATES = ("CLAUDE.md", SETTINGS)
"""The files that hold placeholders; every other file is copied as is."""


def render_librarian_project(
    target: Path,
    *,
    owner: Owner,
    l0: PurePath,
    wiki: PurePath,
    source: Path = LIBRARIAN_PROJECT,
) -> None:
    """Writes a copy of the librarian project to `target`, filled in for this instance.

    `l0` and `wiki` are where the session sees L0 and the wiki. `target` must not exist yet, or
    be empty.
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
            "wiki": str(wiki),
        },
        name="the librarian project",
    )
