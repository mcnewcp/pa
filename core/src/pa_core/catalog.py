"""The catalog: a derived SQLite index of the episodes in L0 (ADR-0001).

It carries no integrity burden: L0 is the truth, and the catalog can be deleted and rebuilt
from the envelopes at any time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from pa_core.envelope import Envelope, Kind, Participant, Role, Source
from pa_core.l0 import L0Store, episode_ref

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_start TEXT NOT NULL,
    occurred_end TEXT,
    calendar_name TEXT,
    content_hash TEXT NOT NULL,
    episode_path TEXT NOT NULL,
    raw_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS participants (
    episode_id TEXT NOT NULL REFERENCES episodes (episode_id),
    position INTEGER NOT NULL,
    identifier TEXT NOT NULL,
    role TEXT NOT NULL,
    name TEXT,
    PRIMARY KEY (episode_id, position)
);
"""


@dataclass(frozen=True)
class CatalogEntry:
    """What the catalog knows about one episode. Paths are relative to the L0 root."""

    episode_id: str
    source: Source
    kind: Kind
    occurred_start: datetime
    occurred_end: datetime | None
    participants: tuple[Participant, ...]
    calendar_name: str | None
    content_hash: str
    episode_path: str
    raw_path: str


class Catalog:
    """The catalog in the SQLite file at `path`, created if it does not exist."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> Catalog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    def add(self, envelope: Envelope) -> None:
        """Index an episode, replacing what the catalog held for its id."""
        self.add_all([envelope])

    def add_all(self, envelopes: Iterable[Envelope]) -> int:
        """Index episodes in one transaction, as `add` would; returns how many."""
        count = 0
        with self._db:
            for envelope in envelopes:
                self._insert(envelope)
                count += 1
        return count

    def _insert(self, envelope: Envelope) -> None:
        key = (envelope.episode_id,)
        self._db.execute("DELETE FROM participants WHERE episode_id = ?", key)
        self._db.execute("DELETE FROM episodes WHERE episode_id = ?", key)
        self._db.execute(
            "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                envelope.episode_id,
                envelope.source,
                envelope.kind,
                _utc_text(envelope.occurred_at.start),
                _utc_text(envelope.occurred_at.end) if envelope.occurred_at.end else None,
                envelope.calendar_name,
                envelope.content_hash,
                episode_ref(envelope),
                envelope.raw_ref,
            ),
        )
        self._db.executemany(
            "INSERT INTO participants VALUES (?, ?, ?, ?, ?)",
            [
                (envelope.episode_id, position, p.identifier, p.role, p.name)
                for position, p in enumerate(envelope.participants)
            ],
        )

    def get(self, episode_id: str) -> CatalogEntry | None:
        row = self._db.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return None if row is None else self._entry(row)

    def entries(self) -> list[CatalogEntry]:
        """Every episode in the catalog, by episode id."""
        rows = self._db.execute("SELECT * FROM episodes ORDER BY episode_id").fetchall()
        return [self._entry(row) for row in rows]

    def _entry(self, row: tuple[str, ...]) -> CatalogEntry:
        episode_id, source, kind, start, end, calendar_name, content_hash, episode, raw = row
        participants = self._db.execute(
            "SELECT identifier, role, name FROM participants WHERE episode_id = ? "
            "ORDER BY position",
            (episode_id,),
        )
        return CatalogEntry(
            episode_id=episode_id,
            source=Source(source),
            kind=Kind(kind),
            occurred_start=datetime.fromisoformat(start),
            occurred_end=datetime.fromisoformat(end) if end else None,
            participants=tuple(
                Participant(identifier=identifier, role=Role(role), name=name)
                for identifier, role, name in participants
            ),
            calendar_name=calendar_name,
            content_hash=content_hash,
            episode_path=episode,
            raw_path=raw,
        )


def _utc_text(moment: datetime) -> str:
    """Fixed-width UTC text, so comparing two values as strings compares the moments."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def rebuild_catalog(path: Path, store: L0Store) -> int:
    """Replace the catalog at `path` with one built from every envelope in L0.

    The new catalog is built beside the old one and swapped in whole. Returns how many
    episodes it holds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    building = path.with_name(f".{path.name}.rebuild")
    _remove_database(building)
    with Catalog(building) as catalog:
        count = catalog.add_all(store.envelopes())
    # A journal left by a crash would otherwise be replayed into the new catalog.
    _remove_database(path)
    building.replace(path)
    return count


def _remove_database(path: Path) -> None:
    for leftover in (path, path.with_name(f"{path.name}-journal")):
        leftover.unlink(missing_ok=True)
