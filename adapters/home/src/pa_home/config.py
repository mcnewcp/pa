"""Instance configuration for the home PA, read from the environment."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_core.errors import PaError
from pa_core.librarian.run import LOOP_AGE_DAYS, PAGE_THRESHOLD
from pa_core.librarian.wiki import GIT_DIR as WIKI_GIT_DIR
from pa_core.librarian.wiki import TREE as WIKI_TREE
from pa_core.owner import Owner

DATA_DIR_VAR = "PA_DATA_DIR"
OWNER_NAME_VAR = "PA_OWNER_NAME"
OWNER_EMAILS_VAR = "PA_OWNER_EMAILS"
OWNER_OTHER_NAMES_VAR = "PA_OWNER_OTHER_NAMES"
TIME_ZONE_VAR = "PA_TIME_ZONE"
# The librarian: its model and effort, the caps on each session, and the command that runs
# `claude` where the sessions are fenced.
LIBRARIAN_MODEL_VAR = "PA_LIBRARIAN_MODEL"
LIBRARIAN_EFFORT_VAR = "PA_LIBRARIAN_EFFORT"
LIBRARIAN_MAX_TURNS_VAR = "PA_LIBRARIAN_MAX_TURNS"
LIBRARIAN_TIMEOUT_VAR = "PA_LIBRARIAN_TIMEOUT_SECONDS"
LIBRARIAN_COMMAND_VAR = "PA_LIBRARIAN_COMMAND"
LIBRARIAN_PAGE_THRESHOLD_VAR = "PA_LIBRARIAN_PAGE_THRESHOLD"
LIBRARIAN_LOOP_AGE_VAR = "PA_LIBRARIAN_LOOP_AGE_DAYS"
# Backup is configured with restic's own variables, so an off-box repository's credentials
# (also restic's own variables) need no translation.
BACKUP_REPOSITORY_VARS = ("RESTIC_REPOSITORY", "RESTIC_REPOSITORY_FILE")
BACKUP_PASSWORD_VARS = ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")


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

    @property
    def catalog(self) -> Path:
        return self.path / "catalog" / "catalog.sqlite"

    @property
    def wiki(self) -> Path:
        """L1: the librarian's wiki."""
        return self.path / WIKI_TREE

    @property
    def wiki_git(self) -> Path:
        """The wiki's git directory, beside the wiki and never inside it."""
        return self.path / WIKI_GIT_DIR


@dataclass(frozen=True)
class LibrarianConfig:
    """How the librarian's sessions run."""

    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_turns: int = 200
    """The most turns one session may take."""
    timeout_seconds: float = 3600
    """The longest one session may run."""
    command: tuple[str, ...] = ("claude",)
    """What runs `claude` for a session: the CLI itself, or a command that reaches its VM."""
    page_threshold: int = PAGE_THRESHOLD
    """The distinct days a slug must be linked on in the day logs before it gets a page."""
    loop_age_days: int = LOOP_AGE_DAYS
    """How long a loop may stay open before lint warns about it."""


def load_data_root(environ: Mapping[str, str] = os.environ) -> DataRoot:
    """The configured data root; refuses one that is unset or inside the repository."""
    value = environ.get(DATA_DIR_VAR, "").strip()
    if not value:
        raise ConfigError(
            f"{DATA_DIR_VAR} is not set. Point it at a directory outside the repository, "
            "for example ~/.local/share/pa-dev/."
        )
    path = Path(value).expanduser().resolve()
    require_outside_repository(
        path, DATA_DIR_VAR, "PA data must live outside it so it can never be committed."
    )
    return DataRoot(path)


def require_outside_repository(path: Path, name: str, reason: str) -> None:
    """Refuses a resolved `path` inside the repository, naming it as `name` and saying why."""
    # The repository holding this code, and the one the command runs in (they differ when
    # the package is installed rather than run from the workspace).
    for start in (Path(__file__), Path.cwd()):
        repository = _repository_root(start)
        if repository is not None and path.is_relative_to(repository):
            raise ConfigError(f"{name} ({path}) is inside the repository ({repository}). {reason}")


def load_owner(environ: Mapping[str, str] = os.environ) -> Owner:
    """The configured owner; refuses to go on without a display name and an email address.

    Email addresses and other names are comma-separated. The first address is how the owner
    appears as a participant.
    """
    name = environ.get(OWNER_NAME_VAR, "").strip()
    email_addresses = _comma_separated(environ, OWNER_EMAILS_VAR)
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
        other_names=_comma_separated(environ, OWNER_OTHER_NAMES_VAR),
    )


def load_time_zone(environ: Mapping[str, str] = os.environ) -> ZoneInfo:
    """The owner's time zone, which decides each episode's day in the wiki."""
    name = environ.get(TIME_ZONE_VAR, "").strip()
    if not name:
        raise ConfigError(
            f"The owner's time zone is missing: set {TIME_ZONE_VAR} to an IANA time zone, "
            "such as America/Chicago."
        )
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"{TIME_ZONE_VAR} is not a known IANA time zone, such as America/Chicago: {name!r}"
        ) from error


def load_librarian_config(environ: Mapping[str, str] = os.environ) -> LibrarianConfig:
    """The librarian's configuration: Opus 5.5 at medium effort unless configured otherwise."""
    default = LibrarianConfig()
    command = environ.get(LIBRARIAN_COMMAND_VAR, "").strip()
    return LibrarianConfig(
        model=environ.get(LIBRARIAN_MODEL_VAR, "").strip() or default.model,
        effort=environ.get(LIBRARIAN_EFFORT_VAR, "").strip() or default.effort,
        max_turns=int(_positive(environ, LIBRARIAN_MAX_TURNS_VAR, default.max_turns)),
        timeout_seconds=_positive(environ, LIBRARIAN_TIMEOUT_VAR, default.timeout_seconds),
        command=tuple(shlex.split(command)) if command else default.command,
        page_threshold=_positive_integer(
            environ, LIBRARIAN_PAGE_THRESHOLD_VAR, default.page_threshold
        ),
        loop_age_days=_positive_integer(environ, LIBRARIAN_LOOP_AGE_VAR, default.loop_age_days),
    )


def require_backup_config(environ: Mapping[str, str] = os.environ) -> None:
    """Refuses to go on unless restic's repository and password are configured."""
    missing = [
        " or ".join(group)
        for group in (BACKUP_REPOSITORY_VARS, BACKUP_PASSWORD_VARS)
        if not any(environ.get(var, "").strip() for var in group)
    ]
    if missing:
        raise ConfigError(
            f"Backup configuration is missing: set {' and '.join(missing)}. "
            "These are restic's own variables: the repository to back up to and its password."
        )


def _positive(environ: Mapping[str, str], var: str, default: float) -> float:
    value = environ.get(var, "").strip()
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        number = 0.0
    if not number > 0:
        raise ConfigError(f"{var} must be a positive number (got {value!r}).")
    return number


def _positive_integer(environ: Mapping[str, str], var: str, default: int) -> int:
    value = environ.get(var, "").strip()
    if not value:
        return default
    if not value.isdigit() or int(value) < 1:
        raise ConfigError(f"{var} must be a whole number of at least 1 (got {value!r}).")
    return int(value)


def _comma_separated(environ: Mapping[str, str], var: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in environ.get(var, "").split(",") if item.strip())


def _repository_root(start: Path) -> Path | None:
    """The git working tree containing `start`, if any."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None
