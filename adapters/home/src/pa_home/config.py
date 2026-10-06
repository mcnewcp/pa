"""Instance configuration for the home PA, read from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pa_core.errors import PaError

DATA_DIR_VAR = "PA_DATA_DIR"


class ConfigError(PaError):
    """Instance configuration that is missing or unsafe."""


@dataclass(frozen=True)
class DataRoot:
    """The one directory all PA data lives under."""

    path: Path

    @property
    def inbox(self) -> Path:
        return self.path / "inbox"

    @property
    def l0(self) -> Path:
        return self.path / "l0"


def load_data_root(environ: Mapping[str, str] = os.environ) -> DataRoot:
    """The configured data root; refuses one that is unset or inside the repository."""
    value = environ.get(DATA_DIR_VAR, "").strip()
    if not value:
        raise ConfigError(
            f"{DATA_DIR_VAR} is not set. Point it at a directory outside the repository, "
            "for example ~/.local/share/pa-dev/."
        )
    path = Path(value).expanduser().resolve()
    repository = repository_root()
    if repository is not None and path.is_relative_to(repository):
        raise ConfigError(
            f"{DATA_DIR_VAR} ({path}) is inside the repository ({repository}). "
            "PA data must live outside it so it can never be committed."
        )
    return DataRoot(path)


def repository_root(start: Path = Path(__file__)) -> Path | None:
    """The git working tree this code runs from, if any."""
    for directory in start.resolve().parents:
        if (directory / ".git").exists():
            return directory
    return None
