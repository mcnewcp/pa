"""Files that are replaced whole or not at all."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomically(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write `data` at `path` through a hidden temporary file renamed into place, so a reader
    sees the old file or the new one, never part of either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temp.chmod(mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
