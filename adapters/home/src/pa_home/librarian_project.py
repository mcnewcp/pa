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

import os
import subprocess
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


def project_files(project: Path = LIBRARIAN_PROJECT) -> dict[str, bytes]:
    """The librarian project's files as committed, by path relative to `project`: what its
    version is a hash of (`pa_core.librarian.version`).

    In a git checkout that tracks `project`, that's the project at HEAD, so uncommitted edits
    and untracked files never change the version. Anywhere else (no git, not a checkout, or a
    copy of the project nothing tracks) it falls back to the files on disk, which hash the same
    as the commit they were copied from.
    """
    return _committed(project) or {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def _committed(project: Path) -> dict[str, bytes] | None:
    """`project`'s files at HEAD, or None if no git checkout tracks it."""
    # The checkout is the one `project` is in, whatever the environment says.
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str, input: bytes | None = None) -> bytes | None:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=project, input=input, capture_output=True, env=environ
            )
        except OSError:
            return None
        return completed.stdout if completed.returncode == 0 else None

    listing = git("ls-tree", "-r", "-z", "HEAD", "--", ".")
    blobs: list[tuple[str, str]] = []
    for entry in (listing or b"").split(b"\0"):
        info, _, path = entry.decode().partition("\t")
        if info.split(" ")[1:2] == ["blob"]:
            blobs.append((path, info.split(" ")[2]))
    if not blobs:
        return None
    batch = git("cat-file", "--batch", input="".join(f"{sha}\n" for _, sha in blobs).encode())
    if batch is None:
        return None
    files: dict[str, bytes] = {}
    offset = 0
    for path, _ in blobs:
        header_end = batch.index(b"\n", offset)
        size = int(batch[offset:header_end].split()[2])
        files[path] = batch[header_end + 1 : header_end + 1 + size]
        offset = header_end + 1 + size + 1  # the content, then a newline
    return files


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
