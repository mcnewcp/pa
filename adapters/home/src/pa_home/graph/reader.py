"""Running Cypher against the current build, read-only.

`CypherRunner` is the seam the domain queries run through, and `QueryRunner` the one the
`query` tool's open-ended queries run through. `CurrentBuild` runs Cypher in this process;
`pa_home.graph.worker.GuardedWorker` runs it in a worker process under `Limits`, and is what
the PA MCP server uses for both.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import ladybug as lb

from pa_core.errors import PaError
from pa_home.graph.layout import DATABASE_FILE, GraphDirectory

type Row = dict[str, Any]

BUFFER_POOL_BYTES = 512 << 20
"""LadybugDB's default buffer pool is 80% of RAM; a reader needs far less."""
QUERY_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class Limits:
    """What one query may use before it is stopped."""

    timeout_seconds: float
    """LadybugDB interrupts the query after this long."""
    memory_bytes: int
    """The worker process is killed when its resident memory passes this."""
    buffer_pool_bytes: int
    """LadybugDB's page cache; a query needing more fails at once with "buffer pool is full"."""
    max_rows: int | None = None
    """Rows past this are not read, and the result says it was truncated."""


class NoBuildError(PaError):
    """There is no published build to read yet."""


class CypherRunner(Protocol):
    def run(self, cypher: str, parameters: Mapping[str, Any] | None = None) -> list[Row]:
        """The query's rows, each a dict by column name, from the current build."""
        ...


@dataclass(frozen=True)
class Fetched:
    columns: list[str]
    rows: list[Row]
    truncated: bool
    """True when the query had more rows than were fetched."""


class QueryRunner(Protocol):
    @property
    def limits(self) -> Limits: ...

    def fetch(self, cypher: str, parameters: Mapping[str, Any] | None = None) -> Fetched:
        """The query's rows, at most `limits.max_rows` of them, and whether there were more."""
        ...


class _Open:
    """One build's read-only database, with the calls using it right now."""

    def __init__(self, build: Path, fts_extension: Path, buffer_pool_bytes: int) -> None:
        self.build = build
        self.database = lb.Database(
            str(build / DATABASE_FILE), read_only=True, buffer_pool_size=buffer_pool_bytes
        )
        connection = lb.Connection(self.database)
        try:
            # Loaded once per database; every later connection to it sees the extension.
            connection.execute(f"LOAD EXTENSION '{string_literal(str(fts_extension))}'")
        finally:
            connection.close()
        self.in_flight = 0
        self.retired = False

    def close(self) -> None:
        self.database.close()


class CurrentBuild:
    """Runs each query against the build `current` points at when the call starts.

    When `current` moves, the next call opens the new build, and the old one is closed once
    the calls using it finish. Each call gets a fresh connection: a second parameterized
    full-text query on one connection crashes LadybugDB 0.21.2 (LadybugDB/ladybug#1105).
    """

    def __init__(
        self,
        graph: GraphDirectory,
        *,
        fts_extension: Path,
        timeout_ms: int = QUERY_TIMEOUT_MS,
        buffer_pool_bytes: int = BUFFER_POOL_BYTES,
    ) -> None:
        self._graph = graph
        self._fts_extension = fts_extension
        self._timeout_ms = timeout_ms
        self._buffer_pool_bytes = buffer_pool_bytes
        self._lock = threading.Lock()
        self._open: _Open | None = None

    def run(self, cypher: str, parameters: Mapping[str, Any] | None = None) -> list[Row]:
        return self.fetch(cypher, parameters).rows

    def fetch(
        self,
        cypher: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> Fetched:
        """The query's rows, or only the first `max_rows`; the rest are never read."""
        opened = self._acquire()
        try:
            connection = lb.Connection(opened.database)
            try:
                connection.set_query_timeout(self._timeout_ms)
                result = connection.execute(cypher, dict(parameters or {}))
                if isinstance(result, list):
                    raise PaError("one statement per query")
                columns = result.get_column_names()
                rows: list[Row] = []
                while result.has_next():
                    if max_rows is not None and len(rows) == max_rows:
                        return Fetched(columns, rows, truncated=True)
                    rows.append(dict(zip(columns, result.get_next(), strict=True)))
                return Fetched(columns, rows, truncated=False)
            finally:
                connection.close()
        finally:
            self._release(opened)

    def close(self) -> None:
        with self._lock:
            if self._open is not None:
                self._open.retired = True
                if self._open.in_flight == 0:
                    self._open.close()
                self._open = None

    def _acquire(self) -> _Open:
        build = self._graph.current_build()
        if build is None:
            raise NoBuildError(
                f"No claims graph has been published at {self._graph.path} yet: "
                "run `pa librarian rebuild`."
            )
        with self._lock:
            if self._open is None or self._open.build != build:
                previous = self._open
                self._open = _Open(build, self._fts_extension, self._buffer_pool_bytes)
                if previous is not None:
                    previous.retired = True
                    if previous.in_flight == 0:
                        previous.close()
            self._open.in_flight += 1
            return self._open

    def _release(self, opened: _Open) -> None:
        with self._lock:
            opened.in_flight -= 1
            if opened.retired and opened.in_flight == 0:
                opened.close()


def string_literal(text: str) -> str:
    """`text` escaped to go between single quotes in Cypher."""
    return text.replace("\\", "\\\\").replace("'", "\\'")
