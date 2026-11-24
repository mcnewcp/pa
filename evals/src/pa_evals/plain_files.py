"""Reading files someone else left, such as a session's output or a part of a case: only
regular files, and never through a link."""

from __future__ import annotations

from pathlib import Path


def regular_files(top: Path) -> list[Path]:
    """Every regular file under the directory `top`, in path order, through no link. None when
    `top` is a link itself, or no directory."""
    if top.is_symlink() or not top.is_dir():
        return []
    return sorted(
        directory / name
        for directory, _, names in top.walk()
        for name in names
        if not (directory / name).is_symlink() and (directory / name).is_file()
    )


def is_text(path: Path) -> bool:
    """Whether the file at `path` is UTF-8 text."""
    try:
        path.read_bytes().decode()
    except UnicodeDecodeError:
        return False
    return True
