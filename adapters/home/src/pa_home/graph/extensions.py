"""LadybugDB's extensions, kept at a pinned local path and loaded from there, never installed.

Every 0.21.x release loads extensions built for v0.21.0. The download URL is mutable, so a
file is used only if its SHA-256 is the one pinned here.
"""

from __future__ import annotations

import hashlib
import os
import platform
import tempfile
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pa_core.errors import PaError

EXTENSIONS_DIR_VAR = "PA_LBUG_EXTENSIONS_DIR"
DEFAULT_EXTENSIONS_DIR = Path("~/.local/share/pa/lbug-extensions")
EXTENSION_VERSION = "v0.21.0"
_URL = "https://extension.ladybugdb.com/{version}/{platform}/{name}/lib{name}.lbug_extension"


class ExtensionError(PaError):
    """An extension that is missing, can't be fetched, or isn't the pinned file."""


@dataclass(frozen=True)
class Extension:
    name: str
    sha256: str


FTS = Extension("fts", "742f2756c2f80bcd1886b6ee0446483038cff0ecf6cf47f698bc6fe855cbaef6")
VECTOR = Extension("vector", "571726cc2ea0d202df1909ae5a3b0528c9dea3ea8d40be6c16b310ec14aa8821")


def extensions_dir(environ: Mapping[str, str] = os.environ) -> Path:
    """Where this machine keeps the pinned extensions: `PA_LBUG_EXTENSIONS_DIR`, or a default."""
    value = environ.get(EXTENSIONS_DIR_VAR, "").strip()
    return Path(value or DEFAULT_EXTENSIONS_DIR).expanduser()


def extension_path(root: Path, extension: Extension) -> Path:
    return (
        root
        / EXTENSION_VERSION.removeprefix("v")
        / _platform()
        / extension.name
        / f"lib{extension.name}.lbug_extension"
    )


def verified(root: Path, extension: Extension) -> Path:
    """The extension's path under `root`, once its file is checked; raises if it isn't there."""
    path = extension_path(root, extension)
    if not path.is_file():
        raise ExtensionError(
            f"The LadybugDB {extension.name} extension is missing at {path}. Run "
            f"`pa librarian rebuild` on a machine that can reach extension.ladybugdb.com, or "
            f"copy the file there (SHA-256 {extension.sha256})."
        )
    _check(path, extension)
    return path


def ensure(root: Path, extension: Extension) -> Path:
    """The extension's path under `root`, downloaded and checked first if it isn't there."""
    path = extension_path(root, extension)
    if not path.is_file():
        _download(path, extension)
    return verified(root, extension)


def _download(path: Path, extension: Extension) -> None:
    url = _URL.format(version=EXTENSION_VERSION, platform=_platform(), name=extension.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(temp_name)
    try:
        # The server refuses urllib's default User-Agent.
        request = urllib.request.Request(url, headers={"User-Agent": "pa-librarian"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:
            out.write(response.read())
        _check(temp, extension)
        temp.replace(path)
    except OSError as error:
        raise ExtensionError(f"Could not fetch {url}: {error}") from error
    finally:
        temp.unlink(missing_ok=True)


def _check(path: Path, extension: Extension) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != extension.sha256:
        raise ExtensionError(
            f"{path} has SHA-256 {digest}, not the pinned {extension.sha256}; not loading it."
        )


def _platform() -> str:
    if platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64"):
        return "linux_amd64"
    raise ExtensionError(
        f"No LadybugDB extensions are pinned for {platform.system()} {platform.machine()}."
    )
