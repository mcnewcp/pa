"""The graph directory: published builds and the `current` pointer to one of them.

    graph/
      builds/<build id>/graph.lbdb      a published build: only this file, read-only
      builds/<build id>.report.json     its build report
      current -> builds/<build id>      a relative symlink, swapped by an atomic rename

A reader resolves `current` on every call, so it moves to a new build without a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DATABASE_FILE = "graph.lbdb"


@dataclass(frozen=True)
class GraphDirectory:
    path: Path

    @property
    def builds(self) -> Path:
        return self.path / "builds"

    @property
    def current(self) -> Path:
        return self.path / "current"

    def build(self, build_id: str) -> Path:
        return self.builds / build_id

    def report(self, build_id: str) -> Path:
        return self.builds / f"{build_id}.report.json"

    def current_build(self) -> Path | None:
        """The directory `current` points at, or None before the first publish."""
        try:
            return self.current.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            return None
