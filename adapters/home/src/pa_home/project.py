"""Rendering a Claude Code project directory for one instance.

The repository's project directories (`agent/`, `librarian/`) are shared by both instances and
name none of them. Some of their files are templates with `${name}` placeholders; rendering
writes a copy with the placeholders filled in and every other file copied as is.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from string import Template

from pa_core.errors import PaError

REPOSITORY = Path(__file__).resolve().parents[4]
"""The repository root, where the shared project directories are."""

SETTINGS = ".claude/settings.json"
"""A project's settings, relative to the project. A `claude -p` session is given them as flag
settings (`--settings`), since print mode never trusts a project folder."""


class ProjectRenderError(PaError):
    """A project could not be rendered."""


def render_project(
    source: Path,
    target: Path,
    *,
    templates: Iterable[str],
    values: Mapping[str, str],
    exclude: Iterable[str] = (),
    name: str = "the project",
) -> None:
    """Writes a copy of `source` to `target` with `templates` filled in from `values`.

    `templates` are paths relative to `source`. Every placeholder in them must have a value (a
    literal `$` is written `$$`). In a `.json` template, each value lands inside a JSON string,
    so it is escaped as one. `exclude` are paths relative to `source` left out of the copy.
    `target` must not exist yet, or be empty: a rendered project is replaced by deleting it and
    rendering again, never by writing over files. `name` says which project in errors.
    """
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ProjectRenderError(
            f"{target} is not empty. Render {name} into a new or empty directory "
            "(delete an old rendered copy first)."
        )
    excluded = {source / path for path in exclude}
    shutil.copytree(
        source,
        target,
        ignore=lambda directory, names: [n for n in names if Path(directory) / n in excluded],
        dirs_exist_ok=True,
    )
    for template in templates:
        path = target / template
        if path.suffix == ".json":
            filled = {key: json.dumps(value)[1:-1] for key, value in values.items()}
        else:
            filled = dict(values)
        path.write_text(Template(path.read_text()).substitute(filled))
