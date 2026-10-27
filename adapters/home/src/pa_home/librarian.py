"""The librarian on the home instance: a run over the data root, or a replay into a fresh wiki,
wired to its home adapters.

A run's wiki and its git directory live in the data root. A replay's live in its target
directory (`ReplayTarget`), beside its run log, and it replays either
the data root's L0 or a corpus ingested into a throwaway data root. Each run (each replay)
renders the librarian project as committed (the project its librarian version names) into a
temporary directory, the sessions' working directory, and runs the sessions with the configured
command.

With a sandbox configured (the librarian's VM), each run (each replay) is wrapped in it:

- `<sandbox> start <l0> <project> <wiki>` is given the host's paths, before any session. It
  makes them visible where the sessions run (in the VM: L0 and the project read-only, the wiki
  writable, and nothing else) and prints, as a JSON object with the keys `l0`, `project` and
  `wiki`, where the sessions see them. The project is rendered for those paths after it starts,
  into the directory it was given.
- `<sandbox> kill` kills every session process in it, when a session runs out of time: killing
  the command that started a session there doesn't reach it, and it must be dead before the run
  discards what it wrote.
- `<sandbox> stop` takes it down afterwards, whatever happened, even after a failed start.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pa_core.catalog import open_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest, is_hidden
from pa_core.librarian.replay import BATCH_DAYS, ReplayOutcome, ReplayTarget, replay_librarian
from pa_core.librarian.run import (
    Librarian,
    LibrarianRunError,
    Phase,
    RunOutcome,
    SessionPaths,
    SessionResult,
    WikiSeedError,
    last_cursor,
    run_librarian,
    seed_wiki,
)
from pa_core.librarian.version import librarian_version
from pa_core.owner import Owner
from pa_core.status import LibrarianRun, WikiStatus, read_status, write_status
from pa_home.agent import wall_clock
from pa_home.capture import failure_reason
from pa_home.claude_session import ClaudeSessionRunner
from pa_home.config import DataRoot, LibrarianConfig
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository
from pa_home.librarian_project import (
    LIBRARIAN_PROJECT,
    project_files,
    render_librarian_project,
)


class ReplayTargetError(PaError):
    """A replay target that is neither fresh nor, when resuming, a replay's."""


class LibrarianSandboxError(PaError):
    """The sandbox the librarian's sessions run in could not be set up or taken down."""


def run(
    data_root: DataRoot,
    *,
    owner: Owner,
    time_zone: ZoneInfo,
    config: LibrarianConfig,
    project: Path = LIBRARIAN_PROJECT,
    clock: Callable[[], datetime] = wall_clock,
) -> RunOutcome:
    """Runs the librarian over the episodes captured since the last run, telling it the clock's
    time as "now". `project` is the librarian project's source, run as committed
    (`project_files`).

    Whatever happens, the run is recorded in the status: the time the wiki is current through
    (its cursor's capture time) and the run's outcome, a failure's reason without content. The
    caller holds the data root's lock, so the status holds still.
    """
    started = clock()
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    try:
        files = project_files(project)
        with _runner(data_root.l0, data_root.wiki, owner, config, files) as runner:
            outcome = run_librarian(
                store=FilesystemL0(data_root.l0),
                owner=owner,
                time_zone=time_zone,
                repository=repository,
                runner=runner,
                librarian=_librarian(config, files),
                now=started.astimezone(time_zone),
                page_threshold=config.page_threshold,
                loop_age_days=config.loop_age_days,
            )
    except Exception as error:
        sessions = error.sessions if isinstance(error, LibrarianRunError) else ()
        failed = LibrarianRun(
            at=started, succeeded=False, reason=_failure_reason(error), cost_usd=_cost(sessions)
        )
        _record_run(data_root, repository, failed, clock)
        raise
    warnings = None if outcome.batch is None else len(outcome.warnings)
    succeeded = LibrarianRun(
        at=started, succeeded=True, cost_usd=_cost(outcome.sessions), warnings=warnings
    )
    _record_run(data_root, repository, succeeded, clock)
    return outcome


def _record_run(
    data_root: DataRoot,
    repository: GitWikiRepository,
    run: LibrarianRun,
    clock: Callable[[], datetime],
) -> None:
    """Writes `run` into the status as the wiki's last run, with the time the wiki is current
    through as last committed."""
    cursor = last_cursor(repository)
    wiki = WikiStatus(current_through=None if cursor is None else cursor.captured_at, last_run=run)
    status = read_status(data_root.status)
    write_status(data_root.status, status.model_copy(update={"wiki": wiki}), at=clock())


def _cost(sessions: Sequence[tuple[Phase, SessionResult]]) -> float | None:
    """What the sessions cost together: None if any didn't report its cost."""
    costs = [result.cost_usd for _, result in sessions]
    if any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def _failure_reason(error: Exception) -> str:
    """Why a run failed, without content: a run's error quotes pages, paths, and what the
    session said, so it's named by where it failed alone."""
    if isinstance(error, LibrarianRunError):
        if error.sessions:
            phase, _ = error.sessions[-1]
            return f"the run failed in its {phase} session and was discarded"
        return "the run failed and was discarded"
    if isinstance(error, LibrarianSandboxError):
        return "the librarian's sandbox could not be set up or taken down"
    return failure_reason(error)


def replay(
    target: Path,
    *,
    data_root: DataRoot | None = None,
    corpus: Sequence[Path] = (),
    owner: Owner,
    time_zone: ZoneInfo,
    config: LibrarianConfig,
    batch_days: int = BATCH_DAYS,
    as_of: datetime | None = None,
    window_start: datetime | None = None,
    resume: bool = False,
    project: Path = LIBRARIAN_PROJECT,
    clock: Callable[[], datetime] = wall_clock,
    progress: Callable[[RunOutcome], None] = lambda _: None,
) -> ReplayOutcome:
    """Replays L0 through the librarian into a fresh wiki in `target`, a batch at a time.

    L0 is the `data_root`'s, or `corpus` (directories of raw payloads, one directory per source
    as in the inbox) ingested into a throwaway data root, captured at `as_of` (else the clock's
    time). With a `window_start`, the first batch is the window start's week, and nothing that
    occurred before it is offered but the calendar events still going at it, such as a series.
    `target` must be new or empty, unless `resume`, when it
    must hold a replay to carry on from its last committed run.
    """
    layout = ReplayTarget(target)
    if resume and not layout.git_dir.is_dir():
        raise ReplayTargetError(f"{target} holds no replay to resume")
    if not resume and target.exists() and any(target.iterdir()):
        raise ReplayTargetError(
            f"{target} isn't empty; a replay runs into a fresh wiki (or give --resume to carry "
            "on with the replay in it)"
        )
    with _l0(data_root, corpus, owner, as_of or clock()) as l0:
        repository = GitWikiRepository.open(layout.tree, layout.git_dir)
        files = project_files(project)
        with _runner(l0, layout.tree, owner, config, files) as runner:
            return replay_librarian(
                store=FilesystemL0(l0),
                owner=owner,
                time_zone=time_zone,
                repository=repository,
                runner=runner,
                librarian=_librarian(config, files),
                run_log=layout.run_log,
                batch_days=batch_days,
                as_of=as_of,
                window_start=window_start,
                page_threshold=config.page_threshold,
                loop_age_days=config.loop_age_days,
                progress=progress,
            )


def seed(
    data_root: DataRoot,
    eval_wiki: Path,
    *,
    owner: Owner,
    captured_through: datetime | None = None,
    clock: Callable[[], datetime] = wall_clock,
) -> str:
    """Seeds the data root's fresh wiki from `eval_wiki` (a replay's target, or an eval wiki
    kept in the evals tree: its `wiki/` and run log), replayed over the same episodes as the
    data root's L0, such as the scale corpus the data root was reseeded with. The commit's
    cursor takes every episode captured up to `captured_through` (all of L0 without it: seed
    right after the reseed). Returns the commit."""
    source = ReplayTarget(eval_wiki).tree
    if not source.is_dir():
        raise WikiSeedError(f"{eval_wiki} has no wiki/: it isn't an eval wiki")
    return seed_wiki(
        store=FilesystemL0(data_root.l0),
        owner=owner,
        repository=GitWikiRepository.open(data_root.wiki, data_root.wiki_git),
        source=source,
        now=clock(),
        captured_through=captured_through,
    )


@contextmanager
def _l0(
    data_root: DataRoot | None, corpus: Sequence[Path], owner: Owner, captured_at: datetime
) -> Iterator[Path]:
    """The data root's L0, or the corpus ingested into a throwaway one."""
    if not corpus:
        if data_root is None:
            raise ReplayTargetError("a replay needs a data root or a corpus")
        yield data_root.l0
        return
    with tempfile.TemporaryDirectory(prefix="pa-replay-") as directory:
        throwaway = DataRoot(Path(directory))
        _copy_into_inbox(corpus, throwaway.inbox)
        store = FilesystemL0(throwaway.l0)
        with open_catalog(throwaway.catalog, store) as catalog:
            ingest(throwaway.inbox, store, catalog, owner, now=lambda: captured_at)
        yield throwaway.l0


def _copy_into_inbox(corpus: Sequence[Path], inbox: Path) -> None:
    for directory in corpus:
        if not directory.is_dir():
            raise ReplayTargetError(f"corpus directory not found: {directory}")
        for payload in sorted(directory.rglob("*")):
            relative = payload.relative_to(directory)
            if payload.is_file() and not is_hidden(relative):
                (inbox / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(payload, inbox / relative)


def _librarian(config: LibrarianConfig, files: Mapping[str, bytes]) -> Librarian:
    """The librarian that runs the project made of `files`: the same files the sessions run."""
    return Librarian(
        version=librarian_version(files, model=config.model, effort=config.effort),
        model=config.model,
        effort=config.effort,
    )


@contextmanager
def _runner(
    l0: Path, wiki: Path, owner: Owner, config: LibrarianConfig, files: Mapping[str, bytes]
) -> Iterator[ClaudeSessionRunner]:
    """A session runner in the librarian project made of `files`, rendered for where the
    sessions see `l0` and `wiki`: in the sandbox, if one is configured, else on the host."""
    with tempfile.TemporaryDirectory(prefix="pa-librarian-") as directory:
        rendered = Path(directory) / "project"
        rendered.mkdir()
        with _sandbox(config.sandbox, l0=l0, project=rendered, wiki=wiki) as seen:
            paths = SessionPaths(l0=seen.l0, wiki=seen.wiki)
            render_librarian_project(
                rendered, owner=owner, l0=paths.l0, wiki=paths.wiki, files=files
            )
            yield ClaudeSessionRunner(
                command=config.command,
                project=rendered,
                session_project=seen.project,
                paths=paths,
                model=config.model,
                effort=config.effort,
                max_turns=config.max_turns,
                timeout_seconds=config.timeout_seconds,
                halt=_halt(config.sandbox),
            )


def _halt(sandbox: Sequence[str] | None) -> Callable[[], None]:
    """What kills a timed-out session where it runs: in the sandbox, `<sandbox> kill`. Without
    one, killing its command's processes killed it already."""
    if sandbox is None:
        return lambda: None

    def halt() -> None:
        _sandbox_command(sandbox, "kill")

    return halt


@dataclass(frozen=True)
class _Seen:
    """Where the sessions see L0, the librarian project, and the wiki."""

    l0: PurePosixPath
    project: PurePosixPath
    wiki: PurePosixPath


@contextmanager
def _sandbox(
    sandbox: Sequence[str] | None, *, l0: Path, project: Path, wiki: Path
) -> Iterator[_Seen]:
    """The sandbox set up for one run (or replay) and taken down after it, and where its
    sessions see `l0`, `project`, and `wiki`. Without a sandbox, they see the host's paths."""
    if sandbox is None:
        yield _Seen(l0=PurePosixPath(l0), project=PurePosixPath(project), wiki=PurePosixPath(wiki))
        return
    try:
        printed = _sandbox_command(sandbox, "start", str(l0), str(project), str(wiki))
        yield _seen(printed)
    finally:
        _sandbox_command(sandbox, "stop")


def _sandbox_command(sandbox: Sequence[str], *args: str) -> str:
    command = [*sandbox, *args]
    try:
        # Its progress goes to stderr, which is the owner's terminal; only its answer is read,
        # and it reads nothing.
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, check=False
        )
    except OSError as error:
        raise LibrarianSandboxError(
            f"Could not run the librarian's sandbox {command[0]!r}: {error}"
        ) from error
    if completed.returncode != 0:
        raise LibrarianSandboxError(
            f"The librarian's sandbox failed to {args[0]} (exit {completed.returncode}): "
            f"{' '.join(command)}"
        )
    return completed.stdout


def _seen(printed: str) -> _Seen:
    """Where the sandbox says its sessions see the store, from what `start` printed."""
    try:
        value = json.loads(printed)
        seen = {key: PurePosixPath(value[key]) for key in ("l0", "project", "wiki")}
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise LibrarianSandboxError(
            "The librarian's sandbox didn't say where its sessions see the store (a JSON object "
            f"with l0, project and wiki): {printed.strip()!r}"
        ) from error
    if not all(path.is_absolute() for path in seen.values()):
        raise LibrarianSandboxError(
            f"The librarian's sandbox gave a relative path for the store: {printed.strip()!r}"
        )
    return _Seen(**seen)
