"""Instance configuration for the home PA, read from the environment."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_core.envelope import Source
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
# The librarian: its model and effort, the caps on each session, the sandbox its sessions run
# in, and the command that runs `claude` there.
LIBRARIAN_MODEL_VAR = "PA_LIBRARIAN_MODEL"
LIBRARIAN_EFFORT_VAR = "PA_LIBRARIAN_EFFORT"
LIBRARIAN_MAX_TURNS_VAR = "PA_LIBRARIAN_MAX_TURNS"
LIBRARIAN_TIMEOUT_VAR = "PA_LIBRARIAN_TIMEOUT_SECONDS"
LIBRARIAN_COMMAND_VAR = "PA_LIBRARIAN_COMMAND"
LIBRARIAN_SANDBOX_VAR = "PA_LIBRARIAN_SANDBOX"
LIBRARIAN_PAGE_THRESHOLD_VAR = "PA_LIBRARIAN_PAGE_THRESHOLD"
LIBRARIAN_LOOP_AGE_VAR = "PA_LIBRARIAN_LOOP_AGE_DAYS"
# Backup is configured with restic's own variables, so an off-box repository's credentials
# (also restic's own variables) need no translation.
BACKUP_REPOSITORY_VARS = ("RESTIC_REPOSITORY", "RESTIC_REPOSITORY_FILE")
BACKUP_PASSWORD_VARS = ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")
# Capture: the vault copy the headless Sync client keeps and its daily-notes folder, and how
# often each source's watermark is expected to move (`PA_<SOURCE>_CADENCE_MINUTES`).
VAULT_DIR_VAR = "PA_VAULT_DIR"
VAULT_DAILY_NOTES_VAR = "PA_VAULT_DAILY_NOTES"
DEFAULT_CADENCE = timedelta(minutes=15)
"""A capture cycle runs every 15 minutes, so each source's watermark moves that often."""
COLLECTED_SOURCES = (Source.GMAIL, Source.ICLOUD_CALENDAR, Source.OBSIDIAN)
"""The sources a collector captures. Chat is captured by the assistant's hook instead."""


def cadence_var(source: Source) -> str:
    """The variable that sets `source`'s expected cadence, in minutes."""
    return f"PA_{source.upper()}_CADENCE_MINUTES"


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

    @property
    def state(self) -> Path:
        """Collector state and pending watermarks: the capture cycle's own records, outside L0."""
        return self.path / "state"

    @property
    def status(self) -> Path:
        """The status file, in a directory of its own so it can be shared read-only."""
        return self.path / "status" / "status.json"

    @property
    def lock(self) -> Path:
        """The one lock on the data root, held by a capture cycle and every other job."""
        return self.path / "pa.lock"


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
    sandbox: tuple[str, ...] | None = None
    """The command that sets up the sandbox the sessions run in, such as the librarian's VM, for
    a run or a replay, and takes it down afterwards (`pa_home.librarian`). None runs the
    sessions on the host."""
    page_threshold: int = PAGE_THRESHOLD
    """The distinct days a slug must be linked on in the day logs before it gets a page."""
    loop_age_days: int = LOOP_AGE_DAYS
    """How long a loop may stay open before lint warns about it."""


@dataclass(frozen=True)
class VaultConfig:
    """The vault copy, which the PA only reads, and the folder its daily notes are in."""

    path: Path
    daily_notes: str
    """The daily-notes folder, relative to the vault, such as `Daily`."""


@dataclass(frozen=True)
class CaptureConfig:
    """What the capture cycle collects, and how often each source is expected to move."""

    vault: VaultConfig | None
    """The vault copy, or None when the vault isn't captured."""
    cadences: Mapping[Source, timedelta]
    """Each collected source's expected cadence."""


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
    """The librarian's configuration: Opus 5.5 at medium effort unless configured otherwise.

    With a sandbox, sessions run `claude` through it (`<sandbox> claude`) unless a command is
    configured too.
    """
    default = LibrarianConfig()
    command = environ.get(LIBRARIAN_COMMAND_VAR, "").strip()
    sandbox = environ.get(LIBRARIAN_SANDBOX_VAR, "").strip()
    sandbox_command = tuple(shlex.split(sandbox)) if sandbox else None
    if command:
        session_command = tuple(shlex.split(command))
    elif sandbox_command:
        session_command = (*sandbox_command, "claude")
    else:
        session_command = default.command
    return LibrarianConfig(
        model=environ.get(LIBRARIAN_MODEL_VAR, "").strip() or default.model,
        effort=environ.get(LIBRARIAN_EFFORT_VAR, "").strip() or default.effort,
        max_turns=_positive_integer(environ, LIBRARIAN_MAX_TURNS_VAR, default.max_turns),
        timeout_seconds=_positive(environ, LIBRARIAN_TIMEOUT_VAR, default.timeout_seconds),
        command=session_command,
        sandbox=sandbox_command,
        page_threshold=_positive_integer(
            environ, LIBRARIAN_PAGE_THRESHOLD_VAR, default.page_threshold
        ),
        loop_age_days=_positive_integer(environ, LIBRARIAN_LOOP_AGE_VAR, default.loop_age_days),
    )


def load_capture_config(environ: Mapping[str, str] = os.environ) -> CaptureConfig:
    """What the capture cycle collects: the vault when its copy is configured, which then needs
    its daily-notes folder too. Every source's cadence is 15 minutes unless configured."""
    vault = None
    vault_dir = environ.get(VAULT_DIR_VAR, "").strip()
    if vault_dir:
        daily_notes = environ.get(VAULT_DAILY_NOTES_VAR, "").strip().strip("/")
        if not daily_notes:
            raise ConfigError(
                f"{VAULT_DIR_VAR} is set but {VAULT_DAILY_NOTES_VAR} is not: set it to the "
                "vault's daily-notes folder, relative to the vault, such as Daily."
            )
        if any(part in ("", ".", "..") for part in daily_notes.split("/")):
            raise ConfigError(
                f"{VAULT_DAILY_NOTES_VAR} must be a folder inside the vault (got {daily_notes!r})."
            )
        vault = VaultConfig(path=Path(vault_dir).expanduser().resolve(), daily_notes=daily_notes)
    cadences = {
        source: timedelta(
            minutes=_positive_integer(
                environ, cadence_var(source), int(DEFAULT_CADENCE.total_seconds() // 60)
            )
        )
        for source in COLLECTED_SOURCES
    }
    return CaptureConfig(vault=vault, cadences=cadences)


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
