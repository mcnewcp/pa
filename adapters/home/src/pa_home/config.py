"""Instance configuration for the home PA, read from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pa_core.errors import PaError
from pa_core.owner import Owner

DATA_DIR_VAR = "PA_DATA_DIR"
OWNER_NAME_VAR = "PA_OWNER_NAME"
OWNER_EMAILS_VAR = "PA_OWNER_EMAILS"
OWNER_OTHER_NAMES_VAR = "PA_OWNER_OTHER_NAMES"


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
    # The repository holding this code, and the one the command runs in (they differ when
    # the package is installed rather than run from the workspace).
    for start in (Path(__file__), Path.cwd()):
        repository = _repository_root(start)
        if repository is not None and path.is_relative_to(repository):
            raise ConfigError(
                f"{DATA_DIR_VAR} ({path}) is inside the repository ({repository}). "
                "PA data must live outside it so it can never be committed."
            )
    return DataRoot(path)


def load_owner(environ: Mapping[str, str] = os.environ) -> Owner:
    """The configured owner; refuses to go on without a display name and an email address.

    Email addresses and other names are comma-separated. The first address is how the owner
    appears as a participant.
    """
    name = environ.get(OWNER_NAME_VAR, "").strip()
    email_addresses = tuple(address.lower() for address in _list(environ, OWNER_EMAILS_VAR))
    missing = [
        var
        for var, value in ((OWNER_NAME_VAR, name), (OWNER_EMAILS_VAR, email_addresses))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Owner configuration is missing: set {' and '.join(missing)}. "
            f"{OWNER_NAME_VAR} is the owner's display name and {OWNER_EMAILS_VAR} their "
            f"comma-separated email addresses ({OWNER_OTHER_NAMES_VAR} is optional)."
        )
    return Owner(
        name=name,
        email_addresses=email_addresses,
        other_names=_list(environ, OWNER_OTHER_NAMES_VAR),
    )


def _list(environ: Mapping[str, str], var: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in environ.get(var, "").split(",") if item.strip())


def _repository_root(start: Path) -> Path | None:
    """The git working tree containing `start`, if any."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None
