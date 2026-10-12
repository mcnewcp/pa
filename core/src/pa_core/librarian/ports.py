"""What the librarian needs from outside: model choices, a cache, and a graph builder."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pa_core.claims.records import GraphRecords
from pa_core.model_client import ModelBackend


@dataclass(frozen=True)
class ModelChoice:
    model: str
    effort: str | None = None

    def __str__(self) -> str:
        return f"{self.model} at {self.effort} effort" if self.effort else self.model


DEFAULT_MODEL = ModelChoice("claude-sonnet-5-5", "medium")


@dataclass(frozen=True)
class LibrarianModels:
    """The model for each stage. Extraction's is set apart so it can move on its own."""

    default: ModelChoice = DEFAULT_MODEL
    extraction: ModelChoice = DEFAULT_MODEL


type BackendFor = Callable[[ModelChoice], ModelBackend]
"""Makes the backend that calls a given model at a given effort."""


class LibrarianCache(Protocol):
    """Each stage's results, by stage name and a key derived from all of the result's inputs."""

    def get(self, stage: str, key: str) -> str | None: ...

    def put(self, stage: str, key: str, value: str) -> None: ...


@dataclass(frozen=True)
class PublishedBuild:
    build_id: str
    node_counts: dict[str, int]
    edge_counts: dict[str, int]


class GraphBuilder(Protocol):
    def build(self, records: GraphRecords) -> PublishedBuild:
        """Build a fresh graph from `records`, check it holds them all, and publish it.

        Raises (and publishes nothing) if any step fails.
        """
        ...
