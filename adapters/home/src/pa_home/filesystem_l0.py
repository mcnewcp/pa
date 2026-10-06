"""L0 on the local filesystem: one write-once file per envelope and per raw payload (ADR-0001)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pa_core.envelope import Envelope

READ_ONLY = 0o444


class FilesystemL0:
    """Envelopes under `episodes/<source>/<YYYY-MM>/`, raw payloads under `raw/...` alike."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, episode_id: str) -> Envelope | None:
        source = episode_id.rsplit("_", 1)[0]
        for path in (self.root / "episodes" / source).glob(f"*/{episode_id}.json"):
            return Envelope.model_validate_json(path.read_bytes())
        return None

    def put(self, envelope: Envelope, raw: bytes) -> None:
        # Raw first: an envelope on disk is what makes an episode exist.
        _write_once(self.root / envelope.raw_ref, raw)
        serialized = envelope.model_dump_json(indent=2) + "\n"
        _write_once(self._episode_path(envelope), serialized.encode())

    def _episode_path(self, envelope: Envelope) -> Path:
        month = envelope.occurred_month_utc
        return self.root / "episodes" / envelope.source / month / f"{envelope.episode_id}.json"


def _write_once(path: Path, data: bytes) -> None:
    """Write via a temporary file and an atomic rename, so a crash never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as temp:
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        temp_path.chmod(READ_ONLY)
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
