"""L0 on the local filesystem: one write-once file per envelope and per raw payload (ADR-0001)."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from pa_core.envelope import Envelope
from pa_core.errors import EpisodeConflictError
from pa_core.l0 import QuarantineRecord, episode_ref, quarantine_record_ref, quarantine_ref

READ_ONLY = 0o444

# Renormalize builds the new envelope tree here, beside `episodes/`, then swaps the two.
_BUILDING = ".episodes.renormalize"
# Where `episodes/` waits while the trees are swapped without renameat2 (see `_exchange`).
_ASIDE = f"{_BUILDING}.swap"
RENORMALIZE_LEFTOVERS = f"{_BUILDING}*"
"""A glob for what an interrupted renormalize can leave in the L0 root, once L0 is opened."""


class FilesystemL0:
    """L0 as files under `root`, laid out by `episode_ref` and `raw_ref`.

    Opening L0 first finishes off an envelope swap that a crash interrupted (see `_exchange`),
    so every command sees one whole envelope tree.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        _recover_interrupted_swap(root)

    def envelopes(self) -> Iterator[Envelope]:
        for path in sorted((self.root / "episodes").glob("*/*/*.json")):
            yield Envelope.model_validate_json(path.read_bytes())

    def get(self, episode_id: str) -> Envelope | None:
        source = episode_id.rsplit("_", 1)[0]
        for path in (self.root / "episodes" / source).glob(f"*/{episode_id}.json"):
            return Envelope.model_validate_json(path.read_bytes())
        return None

    def raw_refs(self) -> Iterator[str]:
        for path in sorted((self.root / "raw").glob("*/*/*")):
            # A hidden file is the temporary file of a write that was interrupted.
            if path.is_file() and not path.name.startswith("."):
                yield path.relative_to(self.root).as_posix()

    def get_raw(self, raw_ref: str) -> bytes | None:
        path = self.root / raw_ref
        return path.read_bytes() if path.is_file() else None

    def put(self, envelope: Envelope, raw: bytes) -> None:
        # Raw first: an envelope on disk is what makes an episode exist.
        _write_once(self.root / envelope.raw_ref, raw)
        _write_once(self.root / episode_ref(envelope), _serialized(envelope))

    def quarantine(self, record: QuarantineRecord, raw: bytes) -> None:
        # Raw first, so a record never points at bytes that were not kept.
        _write_once(self.root / quarantine_ref(record, raw), raw)
        serialized = record.model_dump_json(indent=2) + "\n"
        _write_once(self.root / quarantine_record_ref(record, raw), serialized.encode())

    def replace_envelopes(self, envelopes: Iterable[Envelope]) -> None:
        # The new tree is built whole beside the old one, then the two are exchanged in one
        # rename, so L0 never holds a mix of old and new envelopes.
        episodes = self.root / "episodes"
        building = self.root / _BUILDING
        shutil.rmtree(building, ignore_errors=True)  # left by an interrupted renormalize
        building.mkdir(parents=True)
        try:
            for envelope in envelopes:
                path = building / Path(episode_ref(envelope)).relative_to("episodes")
                _write_once(path, _serialized(envelope))
            episodes.mkdir(exist_ok=True)
            _exchange(building, episodes)
            _fsync_dir(self.root)
        finally:
            # Before the exchange this is the unfinished new tree; after it, the old one.
            shutil.rmtree(building, ignore_errors=True)


def _serialized(envelope: Envelope) -> bytes:
    return (envelope.model_dump_json(indent=2) + "\n").encode()


_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _exchange(a: Path, b: Path) -> None:
    """Swap two directories in place, atomically where the system allows it.

    Linux's renameat2 exchanges them in one step. Elsewhere, or on a filesystem without
    support, they are swapped with three renames, so a crash in between can leave `b` missing
    while both trees are still on disk.
    """
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is not None:
        result = renameat2(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b), _RENAME_EXCHANGE)
        if result == 0:
            return
        error = ctypes.get_errno()
        if error not in (errno.ENOSYS, errno.EINVAL):
            raise OSError(error, os.strerror(error), str(a), None, str(b))
    aside = a.with_name(_ASIDE)
    b.rename(aside)
    a.rename(b)
    aside.rename(a)


def _recover_interrupted_swap(root: Path) -> None:
    """Undo or finish a three-rename swap that stopped part way.

    If `episodes/` is missing, the old tree had been moved aside and the new one not yet moved
    in: put the old one back (the unfinished renormalize can simply be run again). If both are
    there, the new tree is in place and the one aside is the old tree: discard it.
    """
    episodes, aside = root / "episodes", root / _ASIDE
    if not aside.exists():
        return
    if episodes.exists():
        shutil.rmtree(aside)
    else:
        aside.rename(episodes)
        shutil.rmtree(root / _BUILDING, ignore_errors=True)
    _fsync_dir(root)


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
