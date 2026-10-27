"""Collectors: each brings one source's new and changed items into its inbox directory.

A collector is told where its source's inbox directory is (`SourceInbox`, the only place it may
write payloads) and where its own state lives, and it collects, or backfills a window; one that
is also a `Reconciler` reconciles too.
Returning means it succeeded; raising means it failed, and the capture cycle records the failure
without its content. The cycle, not the collector, keeps the watermarks.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from pa_core.atomic import write_atomically
from pa_core.envelope import Source
from pa_core.errors import PaError
from pa_core.normalizers import payload_extension


class CollectionError(PaError):
    """A collection that failed. Its message is recorded in the status, so it never holds
    content: no subjects, names, addresses, bodies, or note text."""


class SourceInbox:
    """One source's inbox directory, the only place its collector writes payloads (ADR-0004).

    Each payload is written under a hidden name and renamed into place, so ingest never reads a
    half-written one.
    """

    def __init__(self, inbox: Path, source: Source) -> None:
        self.source = source
        self.directory = inbox / source
        self.written = 0
        """How many payloads this inbox has been given."""

    def write(self, stem: str, raw: bytes) -> None:
        """Drop `raw` into the inbox as `<stem>.<digest><extension>`.

        The digest of the bytes keeps two versions of one item apart while both wait in the
        inbox; the same bytes written twice are one payload.
        """
        if not stem or "/" in stem or stem.startswith("."):
            raise ValueError(f"not a payload name: {stem!r}")
        digest = hashlib.sha256(raw).hexdigest()[:16]
        target = self.directory / f"{stem}.{digest}{payload_extension(self.source)}"
        write_atomically(target, raw)
        self.written += 1


class Collector(Protocol):
    """Brings one source's new and changed items into its inbox directory."""

    @property
    def source(self) -> Source: ...

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        """Write what's new or changed since the last collection into `inbox`.

        `state` is this collector's own directory under the data root, outside L0, where it
        keeps whatever lets it resume (positions, what it last captured). `now` is the clock.
        Raises on failure; a failed collection may have written some payloads.
        """
        ...

    def backfill(
        self,
        inbox: SourceInbox,
        state: Path,
        *,
        window_start: datetime,
        now: Callable[[], datetime],
    ) -> None:
        """Write the source's history from `window_start` on into `inbox`, as a one-time
        capture before collections take over (or to widen the window later).

        Afterwards, collections carry on from where the backfill stopped, and don't reach
        back before `window_start`. Raises on failure, as `collect` does.
        """
        ...


@runtime_checkable
class Reconciler(Protocol):
    """A collector that can also reconcile: compare everything its source holds against what it
    has captured, so a change or a deletion it missed still lands. The nightly job asks for it."""

    def reconcile(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        """Land whatever differs from what was last captured, as `collect` lands what's new.
        Raises on failure, as `collect` does."""
        ...


def read_state[M: BaseModel](path: Path, model: type[M]) -> M:
    """The state saved at `path`, or a fresh `model()` if none has been saved."""
    if not path.exists():
        return model()
    return model.model_validate_json(path.read_bytes())


def write_state(path: Path, state: BaseModel) -> None:
    """Save `state` at `path`, replacing what was there in one step, readable only by the owner
    (a collector's state may hold what it last captured)."""
    write_atomically(path, (state.model_dump_json(indent=2) + "\n").encode(), mode=0o600)
