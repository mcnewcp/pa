"""Cypher in a guarded worker process, so a bad query can't crash, hang, or bloat the server.

`GuardedWorker` is a `CypherRunner` and a `QueryRunner`. It starts `python -m
pa_home.graph.worker` on first use and sends it one query at a time over a pipe. The worker
holds a `CurrentBuild`, so it opens the build read-only with an explicit buffer pool, follows
`current` between calls, and gives LadybugDB a query timeout. Three limits stop a query:

- LadybugDB interrupts it at `timeout_seconds`, or fails it at once when it needs more than
  `buffer_pool_bytes` of pages. The worker survives both.
- The worker is killed if its resident memory passes `memory_bytes`, or if no answer comes
  `GRACE_SECONDS` after the timeout, and a new one is started on the next call.
- If the engine crashes (LadybugDB/ladybug#1105 segfaults the process), only the worker dies.

Every way a query is stopped raises `QueryStopped`; LadybugDB's own errors (a syntax error, a
missing property) raise `CypherError`. Both carry a message meant for the assistant.
"""

from __future__ import annotations

import json
import os
import pickle
import select
import struct
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

from pa_core.errors import PaError
from pa_home.graph.layout import GraphDirectory
from pa_home.graph.reader import CurrentBuild, Fetched, Limits, Row

QUERY_LIMITS = Limits(
    timeout_seconds=10, memory_bytes=1 << 30, buffer_pool_bytes=256 << 20, max_rows=200
)
"""The `query` tool's limits. 256 MB of buffer pool ran real path queries over the scale-sized
spike graph; a runaway walk fills it in seconds and fails cleanly."""

DOMAIN_LIMITS = Limits(timeout_seconds=10, memory_bytes=1 << 30, buffer_pool_bytes=512 << 20)
"""The domain queries' limits. Their rows are never capped: each returns what its rules say."""

GRACE_SECONDS = 5.0
"""How long past the timeout to wait for the worker before killing it."""

_POLL_SECONDS = 0.05
_HEADER = struct.Struct(">Q")
_PAGE_BYTES = os.sysconf("SC_PAGE_SIZE")


class QueryStopped(PaError):
    """A query stopped by a limit, or by a crash of the database engine."""


class CypherError(PaError):
    """LadybugDB refused or failed the query; its message says why."""


class GuardedWorker:
    """Runs Cypher in a worker process under `limits`, one query at a time."""

    def __init__(self, graph: GraphDirectory, *, fts_extension: Path, limits: Limits) -> None:
        self._config = json.dumps(
            {
                "graph": str(graph.path),
                "fts_extension": str(fts_extension),
                "timeout_ms": round(limits.timeout_seconds * 1000),
                "buffer_pool_bytes": limits.buffer_pool_bytes,
            }
        )
        self._limits = limits
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._finalizer: weakref.finalize | None = None

    @property
    def limits(self) -> Limits:
        return self._limits

    def run(self, cypher: str, parameters: Mapping[str, Any] | None = None) -> list[Row]:
        return self.fetch(cypher, parameters).rows

    def fetch(self, cypher: str, parameters: Mapping[str, Any] | None = None) -> Fetched:
        request = (cypher, dict(parameters or {}), self._limits.max_rows)
        with self._lock:
            reply = self._exchange(request)
        status, value = reply
        if status == "ok":
            return value
        if status == "error":
            raise PaError(value)
        raise _engine_error(value, self._limits)

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _exchange(self, request: tuple[str, dict[str, Any], int | None]) -> tuple[str, Any]:
        process = self._running()
        try:
            _send(cast(IO[bytes], process.stdin), request)
        except BrokenPipeError:
            # The worker died between calls; a new one gets the request.
            self._stop()
            process = self._running()
            _send(cast(IO[bytes], process.stdin), request)
        deadline = time.monotonic() + self._limits.timeout_seconds + GRACE_SECONDS
        replies = cast(IO[bytes], process.stdout)
        while True:
            ready, _, _ = select.select([replies], [], [], _POLL_SECONDS)
            if ready:
                reply = _receive(replies)
                if reply is not None:
                    return reply
                self._stop()
                raise QueryStopped(
                    "The database engine crashed running this query, and was restarted; "
                    "the next call will work. Don't retry this query as it is."
                )
            if _resident_bytes(process.pid) > self._limits.memory_bytes:
                self._stop()
                raise QueryStopped(
                    f"The query was stopped: it used more than "
                    f"{_megabytes(self._limits.memory_bytes)} of memory. "
                    "Bound path lengths, filter earlier, or aggregate."
                )
            if time.monotonic() > deadline:
                self._stop()
                raise QueryStopped(
                    f"The query was stopped: it ran longer than "
                    f"{self._limits.timeout_seconds:g} s. Bound path lengths or filter earlier."
                )

    def _running(self) -> subprocess.Popen[bytes]:
        if self._process is None or self._process.poll() is not None:
            self._stop()
            self._process = subprocess.Popen(
                [sys.executable, "-m", "pa_home.graph.worker", self._config],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                bufsize=0,
            )
            self._finalizer = weakref.finalize(self, _kill, self._process)
        return self._process

    def _stop(self) -> None:
        if self._finalizer is not None:
            self._finalizer()
        self._process = None
        self._finalizer = None


def _engine_error(message: str, limits: Limits) -> PaError:
    if message.startswith("Interrupted"):
        return QueryStopped(
            f"The query was stopped: it ran longer than {limits.timeout_seconds:g} s. "
            "Bound path lengths or filter earlier."
        )
    if "buffer pool is full" in message:
        return QueryStopped(
            f"The query was stopped: it needed more than "
            f"{_megabytes(limits.buffer_pool_bytes)} of database memory. Bound path lengths, "
            "filter earlier, or aggregate."
        )
    return CypherError(f"LadybugDB: {message}")


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()


def _resident_bytes(pid: int) -> int:
    try:
        with Path(f"/proc/{pid}/statm").open() as statm:
            return int(statm.read().split()[1]) * _PAGE_BYTES
    except (OSError, IndexError, ValueError):
        return 0


def _megabytes(count: int) -> str:
    return f"{count >> 20} MB"


def _send(stream: IO[bytes], message: Any) -> None:
    body = pickle.dumps(message)
    stream.write(_HEADER.pack(len(body)) + body)
    stream.flush()


def _receive(stream: IO[bytes]) -> Any | None:
    """The next message, or None at the end of the stream."""
    header = _read_exactly(stream, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    body = _read_exactly(stream, length)
    return None if body is None else pickle.loads(body)


def _read_exactly(stream: IO[bytes], count: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _serve(config: dict[str, Any]) -> None:
    """The worker's loop: answer each request on stdin until it closes."""
    # Replies get their own descriptor, and anything else written to stdout goes to stderr,
    # so a stray print can't corrupt them.
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    requests = os.fdopen(sys.stdin.fileno(), "rb", buffering=0)
    build = CurrentBuild(
        GraphDirectory(Path(config["graph"])),
        fts_extension=Path(config["fts_extension"]),
        timeout_ms=config["timeout_ms"],
        buffer_pool_bytes=config["buffer_pool_bytes"],
    )
    while (request := _receive(requests)) is not None:
        cypher, parameters, max_rows = request
        try:
            reply: tuple[str, Any] = ("ok", build.fetch(cypher, parameters, max_rows=max_rows))
        except PaError as error:
            reply = ("error", str(error))
        except RuntimeError as error:
            reply = ("engine", str(error))
        _send(replies, reply)
    build.close()


if __name__ == "__main__":
    _serve(json.loads(sys.argv[1]))
