"""The librarian version: which instructions, model, and effort a run used.

It's a hash of the librarian project's source files (as committed to the repository, never a
rendered copy, which holds instance values), the model, and the effort. Nobody has to remember
to bump it: any change to one of them changes it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

LENGTH = 12
"""Hex digits kept: enough to tell versions apart, short enough to read."""


def librarian_version(project: Path, *, model: str, effort: str) -> str:
    """The version of the librarian in `project`, run on `model` at `effort`."""
    digest = hashlib.sha256()
    for path in sorted(p for p in project.rglob("*") if p.is_file()):
        content = path.read_bytes()
        digest.update(f"{path.relative_to(project).as_posix()}\0{len(content)}\0".encode())
        digest.update(content)
    digest.update(f"model\0{model}\0effort\0{effort}\0".encode())
    return digest.hexdigest()[:LENGTH]
