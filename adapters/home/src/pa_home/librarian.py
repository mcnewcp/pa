"""The librarian on the home instance: a run over the data root, or a replay into a fresh wiki,
wired to its home adapters.

A run's wiki and its git directory live in the data root. A replay's live in its target
directory (`ReplayTarget`), beside its run log, and it replays either
the data root's L0 or a corpus ingested into a throwaway data root. Each run (each replay)
renders the librarian project into a temporary directory, the sessions' working directory, and
runs the sessions with the configured command.

With a sandbox configured (the librarian's VM), each run (each replay) is wrapped in it:

- `<sandbox> start <l0> <project> <wiki>` is given the host's paths, before any session. It
  makes them visible where the sessions run (in the VM: L0 and the project read-only, the wiki
  writable, and nothing else) and prints, as a JSON object with the keys `l0`, `project` and
  `wiki`, where the sessions see them. The project is rendered for those paths after it starts,
  into the directory it was given.
- `<sandbox> stop` takes it down afterwards, whatever happened, even after a failed start.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pa_core.catalog import open_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest, is_hidden
from pa_core.librarian.replay import BATCH_DAYS, ReplayOutcome, ReplayTarget, replay_librarian
from pa_core.librarian.run import Librarian, RunOutcome, SessionPaths, run_librarian
from pa_core.librarian.version import librarian_version
from pa_core.owner import Owner
from pa_home.agent import wall_clock
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
    time as "now". `project` is the librarian project's source."""
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    with _runner(data_root.l0, data_root.wiki, owner, config, project) as runner:
        return run_librarian(
            store=FilesystemL0(data_root.l0),
            owner=owner,
            time_zone=time_zone,
            repository=repository,
            runner=runner,
            librarian=_librarian(config, project),
            now=clock().astimezone(time_zone),
            page_threshold=config.page_threshold,
            loop_age_days=config.loop_age_days,
        )


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
    resume: bool = False,
    project: Path = LIBRARIAN_PROJECT,
    clock: Callable[[], datetime] = wall_clock,
    progress: Callable[[RunOutcome], None] = lambda _: None,
) -> ReplayOutcome:
    """Replays L0 through the librarian into a fresh wiki in `target`, a batch at a time.

    L0 is the `data_root`'s, or `corpus` (directories of raw payloads, one directory per source
    as in the inbox) ingested into a throwaway data root, captured at `as_of` (else the clock's
    time). `target` must be new or empty, unless `resume`, when it must hold a replay to carry
    on from its last committed run.
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
        with _runner(l0, layout.tree, owner, config, project) as runner:
            return replay_librarian(
                store=FilesystemL0(l0),
                owner=owner,
                time_zone=time_zone,
                repository=repository,
                runner=runner,
                librarian=_librarian(config, project),
                run_log=layout.run_log,
                batch_days=batch_days,
                as_of=as_of,
                page_threshold=config.page_threshold,
                loop_age_days=config.loop_age_days,
                progress=progress,
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


def _librarian(config: LibrarianConfig, project: Path) -> Librarian:
    return Librarian(
        version=librarian_version(project_files(project), model=config.model, effort=config.effort),
        model=config.model,
        effort=config.effort,
    )


@contextmanager
def _runner(
    l0: Path, wiki: Path, owner: Owner, config: LibrarianConfig, project: Path
) -> Iterator[ClaudeSessionRunner]:
    """A session runner in the librarian project, rendered for where the sessions see `l0` and
    `wiki`: in the sandbox, if one is configured, else on the host."""
    with tempfile.TemporaryDirectory(prefix="pa-librarian-") as directory:
        rendered = Path(directory) / "project"
        rendered.mkdir()
        with _sandbox(config.sandbox, l0=l0, project=rendered, wiki=wiki) as seen:
            paths = SessionPaths(l0=seen.l0, wiki=seen.wiki)
            render_librarian_project(
                rendered, owner=owner, l0=paths.l0, wiki=paths.wiki, source=project
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
            )


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
        # Its progress goes to stderr, which is the owner's terminal; only its answer is read.
        completed = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
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
