"""The librarian version: which instructions, model, and effort a run used.

It's a hash of the librarian project's source files (as committed to the repository, never a
rendered copy, which holds instance values, nor uncommitted edits), the model, and the effort.
Nobody has to remember to bump it: any committed change to one of them changes it. Reading the
files as committed is the adapter's (`pa_home.librarian_project.project_files`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

LENGTH = 12
"""Hex digits kept: enough to tell versions apart, short enough to read."""


def librarian_version(files: Mapping[str, bytes], *, model: str, effort: str) -> str:
    """The version of the librarian project made of `files` (each file's content by its path
    relative to the project, `/`-separated), run on `model` at `effort`."""
    digest = hashlib.sha256()
    for path in sorted(files):
        content = files[path]
        digest.update(f"{path}\0{len(content)}\0".encode())
        digest.update(content)
    digest.update(f"model\0{model}\0effort\0{effort}\0".encode())
    return digest.hexdigest()[:LENGTH]
