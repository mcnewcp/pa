"""The librarian on the home instance: a run over the data root, wired to its home adapters.

The wiki and its git directory live in the data root. Each run renders the librarian project
into a temporary directory, the sessions' working directory for that run, and runs the
sessions with the configured command.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pa_core.librarian.run import Librarian, RunOutcome, SessionPaths, run_librarian
from pa_core.librarian.version import librarian_version
from pa_core.owner import Owner
from pa_home.agent import wall_clock
from pa_home.claude_session import ClaudeSessionRunner
from pa_home.config import DataRoot, LibrarianConfig
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository
from pa_home.librarian_project import LIBRARIAN_PROJECT, render_librarian_project


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
    librarian = Librarian(
        version=librarian_version(project, model=config.model, effort=config.effort),
        model=config.model,
        effort=config.effort,
    )
    # The sessions run on the host for now, so they see L0 and the wiki where the host does.
    paths = SessionPaths(l0=PurePosixPath(data_root.l0), wiki=PurePosixPath(data_root.wiki))
    with tempfile.TemporaryDirectory(prefix="pa-librarian-") as directory:
        rendered = Path(directory) / "project"
        render_librarian_project(
            rendered, owner=owner, l0=paths.l0, wiki=paths.wiki, source=project
        )
        runner = ClaudeSessionRunner(
            command=config.command,
            project=rendered,
            paths=paths,
            model=config.model,
            effort=config.effort,
            max_turns=config.max_turns,
            timeout_seconds=config.timeout_seconds,
        )
        return run_librarian(
            store=FilesystemL0(data_root.l0),
            time_zone=time_zone,
            repository=repository,
            runner=runner,
            librarian=librarian,
            now=clock().astimezone(time_zone),
        )
