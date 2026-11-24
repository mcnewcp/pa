"""Instance configuration for the home PA, read from the environment."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_core.envelope import Source
from pa_core.errors import PaError
from pa_core.librarian.dreaming import DREAMING_SETTINGS, DreamingSettings, ProposalCaps
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
LIBRARIAN_STALE_LATER_DAYS_VAR = "PA_LIBRARIAN_STALE_LATER_DAYS"
LIBRARIAN_QUIET_DAYS_VAR = "PA_LIBRARIAN_QUIET_DAYS"
# Dreaming's candidates: the most a run is offered.
LIBRARIAN_CANDIDATE_CAP_VAR = "PA_LIBRARIAN_CANDIDATE_CAP"
# Dreaming's proposals: the most a run may add, and the most that may be open at once.
LIBRARIAN_NEW_PROPOSALS_VAR = "PA_LIBRARIAN_NEW_PROPOSALS"
LIBRARIAN_OPEN_PROPOSALS_VAR = "PA_LIBRARIAN_OPEN_PROPOSALS"
# Dreaming in the nightly job: how many days apart its runs are, and 0 for never.
LIBRARIAN_DREAMING_INTERVAL_VAR = "PA_LIBRARIAN_DREAMING_INTERVAL_DAYS"
# Backup is configured with restic's own variables, so an off-box repository's credentials
# (also restic's own variables) need no translation.
BACKUP_REPOSITORY_VARS = ("RESTIC_REPOSITORY", "RESTIC_REPOSITORY_FILE")
BACKUP_PASSWORD_VARS = ("RESTIC_PASSWORD", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD_COMMAND")
# Capture: the vault copy the headless Sync client keeps and its daily-notes folder, and how
# often each source's watermark is expected to move (`PA_<SOURCE>_CADENCE_MINUTES`).
VAULT_DIR_VAR = "PA_VAULT_DIR"
VAULT_DAILY_NOTES_VAR = "PA_VAULT_DAILY_NOTES"
# Gmail: the OAuth token `pa gmail auth` saves (Gmail is captured when it's set), and the OAuth
# client from the owner's Cloud project that the consent needs. Both live in the owner's
# configuration directory, readable only by them.
GMAIL_TOKEN_VAR = "PA_GMAIL_TOKEN"
GMAIL_CLIENT_VAR = "PA_GMAIL_CLIENT"
DEFAULT_CADENCE = timedelta(minutes=15)
"""A capture cycle runs every 15 minutes, so each source's watermark moves that often."""
COLLECTED_SOURCES = (Source.GMAIL, Source.ICLOUD_CALENDAR, Source.OBSIDIAN)
"""The sources a collector captures. Chat is captured by the assistant's hook instead."""
# iCloud Calendar over CalDAV: the Apple ID, the file holding its app-specific password, and
# the calendars to capture, by display name (iCloud Calendar is captured when they're set).
ICLOUD_USERNAME_VAR = "PA_ICLOUD_USERNAME"
ICLOUD_CALENDARS_VAR = "PA_ICLOUD_CALENDARS"
ICLOUD_PASSWORD_FILE_VAR = "PA_ICLOUD_PASSWORD_FILE"
DEFAULT_ICLOUD_PASSWORD_FILE = "~/.config/pa/icloud-app-password"


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
    def evals(self) -> Path:
        """The private eval directory: real-data eval sets, cuts, runs, and verdicts, which
        never go in the repository."""
        return self.path / "evals"

    @property
    def diagnoses(self) -> Path:
        """The private diagnoses directory: one directory per diagnosis, holding its referral,
        the session's output (a private analysis and the findings as it left them), and the
        session's record. It quotes real content, so no command prints any of it."""
        return self.path / "diagnoses"

    @property
    def run_errors(self) -> Path:
        """Where the whole error of the last failed librarian run, and of the last failed
        dreaming run, is kept for the owner (`pa_home.librarian`): it quotes pages, so no
        command prints it."""
        return self.path / "run-errors"

    @property
    def replays(self) -> Path:
        """Where replays build their wikis (`pa librarian replay`), each beside its run log."""
        return self.path / "replays"

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


DREAMING_INTERVAL_DAYS = 7
"""How many days apart the nightly job's dreaming runs are, unless configured otherwise."""


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
    dreaming: DreamingSettings = DREAMING_SETTINGS
    """What a dreaming run goes by: the thresholds its candidates are measured against, the
    cap on them, and the caps on its proposals. A librarian run's lint goes by the first two
    of its thresholds (`page_threshold`, `loop_age_days`)."""
    dreaming_interval_days: int = DREAMING_INTERVAL_DAYS
    """How many days apart the nightly job's dreaming runs are; 0 turns the step off. A run by
    hand (`pa librarian dream`) doesn't ask."""

    @property
    def page_threshold(self) -> int:
        """The distinct days a slug must be linked on in the day logs before it gets a page."""
        return self.dreaming.page_threshold

    @property
    def loop_age_days(self) -> int:
        """How long a loop may stay open before lint warns about it."""
        return self.dreaming.loop_age_days


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
    gmail_token: Path | None = None
    """Gmail's OAuth token, or None when Gmail isn't captured."""
    icloud_calendars: tuple[str, ...] = ()
    """The iCloud calendars to capture, by display name; none when iCloud isn't captured."""


@dataclass(frozen=True)
class GmailConfig:
    """Where Gmail's OAuth client and token are."""

    token: Path
    client: Path | None
    """The OAuth client (a Desktop app client's JSON), needed only to ask for consent."""


@dataclass(frozen=True)
class ICloudCredentials:
    """The Apple ID and its app-specific password, which CalDAV accepts in place of the Apple ID
    password. Neither is ever printed."""

    username: str = field(repr=False)
    password: str = field(repr=False)


def load_icloud_credentials(environ: Mapping[str, str] = os.environ) -> ICloudCredentials:
    """The iCloud credential: the Apple ID, and the app-specific password from a file only the
    owner can read (by default in the owner's configuration directory)."""
    username = environ.get(ICLOUD_USERNAME_VAR, "").strip()
    if not username:
        raise ConfigError(f"{ICLOUD_USERNAME_VAR} is not set: set it to the owner's Apple ID.")
    configured = environ.get(ICLOUD_PASSWORD_FILE_VAR, "").strip()
    path = Path(configured or DEFAULT_ICLOUD_PASSWORD_FILE).expanduser()
    try:
        mode = path.stat().st_mode
        password = path.read_text().strip()
    except FileNotFoundError:
        raise ConfigError(
            f"There's no app-specific password at {path}. Create one for the Apple ID at "
            f"account.apple.com and save it there with mode 600, or point "
            f"{ICLOUD_PASSWORD_FILE_VAR} at the file that holds it."
        ) from None
    except OSError as error:
        raise ConfigError(
            f"The app-specific password at {path} can't be read: {error.strerror}"
        ) from None
    if mode & 0o077:
        raise ConfigError(
            f"The app-specific password at {path} is readable by others (mode "
            f"{mode & 0o777:o}): make it readable only by the owner (chmod 600)."
        )
    if not password:
        raise ConfigError(f"The app-specific password file {path} is empty.")
    return ICloudCredentials(username=username, password=password)


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
    unset = default.dreaming
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
        dreaming=DreamingSettings(
            page_threshold=_positive_integer(
                environ, LIBRARIAN_PAGE_THRESHOLD_VAR, unset.page_threshold
            ),
            loop_age_days=_positive_integer(environ, LIBRARIAN_LOOP_AGE_VAR, unset.loop_age_days),
            stale_later_days=_positive_integer(
                environ, LIBRARIAN_STALE_LATER_DAYS_VAR, unset.stale_later_days
            ),
            quiet_days=_positive_integer(environ, LIBRARIAN_QUIET_DAYS_VAR, unset.quiet_days),
            candidate_cap=_positive_integer(
                environ, LIBRARIAN_CANDIDATE_CAP_VAR, unset.candidate_cap
            ),
            proposal_caps=ProposalCaps(
                new=_positive_integer(
                    environ, LIBRARIAN_NEW_PROPOSALS_VAR, unset.proposal_caps.new
                ),
                open=_positive_integer(
                    environ, LIBRARIAN_OPEN_PROPOSALS_VAR, unset.proposal_caps.open
                ),
            ),
        ),
        dreaming_interval_days=_whole_number(
            environ, LIBRARIAN_DREAMING_INTERVAL_VAR, default.dreaming_interval_days
        ),
    )


def load_capture_config(environ: Mapping[str, str] = os.environ) -> CaptureConfig:
    """What the capture cycle collects: the vault when its copy is configured, which then needs
    its daily-notes folder too; Gmail when its token is; iCloud Calendar when its calendars
    are. Every source's cadence is 15 minutes unless configured."""
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
    gmail_token = _path(environ, GMAIL_TOKEN_VAR)
    return CaptureConfig(
        vault=vault,
        cadences=cadences,
        gmail_token=gmail_token,
        icloud_calendars=load_icloud_calendars(environ, required=False),
    )


def load_icloud_calendars(
    environ: Mapping[str, str] = os.environ, *, required: bool = True
) -> tuple[str, ...]:
    """The iCloud calendars to capture, by their display names, comma-separated."""
    calendars = tuple(dict.fromkeys(_comma_separated(environ, ICLOUD_CALENDARS_VAR)))
    if required and not calendars:
        raise ConfigError(
            f"{ICLOUD_CALENDARS_VAR} is not set: set it to the calendars to capture, by the "
            "names the Calendar app shows, comma-separated, such as Home,Family Calendar."
        )
    return calendars


def load_gmail_config(environ: Mapping[str, str] = os.environ) -> GmailConfig:
    """Where Gmail's token is (it must be configured) and its OAuth client, if configured."""
    token = _path(environ, GMAIL_TOKEN_VAR)
    if token is None:
        raise ConfigError(
            f"{GMAIL_TOKEN_VAR} is not set: set it to where Gmail's OAuth token is kept, in "
            "your configuration directory, such as ~/.config/pa/gmail-token.json."
        )
    return GmailConfig(token=token, client=_path(environ, GMAIL_CLIENT_VAR))


def _path(environ: Mapping[str, str], var: str) -> Path | None:
    value = environ.get(var, "").strip()
    return Path(value).expanduser().resolve() if value else None


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


def _whole_number(environ: Mapping[str, str], var: str, default: int) -> int:
    """As `_positive_integer`, for a setting whose 0 means off."""
    value = environ.get(var, "").strip()
    if not value:
        return default
    if not value.isdigit():
        raise ConfigError(f"{var} must be a whole number, 0 or more (got {value!r}).")
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
