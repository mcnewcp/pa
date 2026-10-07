"""L0 on the local filesystem: one write-once file per envelope and per raw payload (ADR-0001)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from pa_core.envelope import Envelope
from pa_core.errors import EpisodeConflictError
from pa_core.l0 import QuarantineRecord, episode_ref, quarantine_record_ref, quarantine_ref

READ_ONLY = 0o444


class FilesystemL0:
    """L0 as files under `root`, laid out by `episode_ref` and `raw_ref`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def envelopes(self) -> Iterator[Envelope]:
        for path in sorted((self.root / "episodes").glob("*/*/*.json")):
            yield Envelope.model_validate_json(path.read_bytes())

    def get(self, episode_id: str) -> Envelope | None:
        source = episode_id.rsplit("_", 1)[0]
        for path in (self.root / "episodes" / source).glob(f"*/{episode_id}.json"):
            return Envelope.model_validate_json(path.read_bytes())
        return None

    def get_raw(self, raw_ref: str) -> bytes | None:
        path = self.root / raw_ref
        return path.read_bytes() if path.is_file() else None

    def put(self, envelope: Envelope, raw: bytes) -> None:
        # Raw first: an envelope on disk is what makes an episode exist.
        _write_once(self.root / envelope.raw_ref, raw)
        serialized = envelope.model_dump_json(indent=2) + "\n"
        _write_once(self.root / episode_ref(envelope), serialized.encode())

    def quarantine(self, record: QuarantineRecord, raw: bytes) -> None:
        # Raw first, so a record never points at bytes that were not kept.
        _write_once(self.root / quarantine_ref(record, raw), raw)
        serialized = record.model_dump_json(indent=2) + "\n"
        _write_once(self.root / quarantine_record_ref(record, raw), serialized.encode())


def _write_once(path: Path, data: bytes) -> None:
    """Write a read-only file that appears whole or not at all, and never replaces one.

    The data goes to a temporary file first and is hard-linked into place, which fails rather
    than overwrite. A file left by an interrupted ingest is kept if it holds the same bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        temp_path.chmod(READ_ONLY)
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise EpisodeConflictError(
                    f"{path.name} already exists in L0 with different bytes"
                ) from None
    finally:
        temp_path.unlink(missing_ok=True)
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
