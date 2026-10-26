"""The repository guard: nothing from real data may sit where it could be committed.

Real-data eval sets, cuts, run directories, and verdict details are kept under the data root.
Every command that reads or writes one refuses a path inside the repository (the one holding
this code, or the one it runs in), after following links, before it touches anything.
"""

from __future__ import annotations

from pathlib import Path

from pa_home.config import require_outside_repository

REASON = (
    "Real-data eval sets, cuts, run directories, and verdict details are kept under the data "
    "root, so nothing real can ever be committed."
)


def require_private(path: Path, name: str) -> Path:
    """`path`, resolved; refused (ConfigError) if it is inside the repository. `name` says
    which path it is, such as the option that gave it."""
    resolved = path.expanduser().resolve()
    require_outside_repository(resolved, name, REASON)
    return resolved
