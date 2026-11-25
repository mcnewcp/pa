"""Dreaming a copy: a dreaming run over a copy of a replay target, of a cut's wiki, or of a
wiki no librarian built, at the copy's as-of time (spec #140).

It is how dreaming's effect is measured: the eval harness asks the same questions with a wiki
and with a dreamed copy of it. The copy is laid out as a replay target is
(`pa_core.librarian.replay.ReplayTarget`): the wiki's tree, its git directory, and the run log.

A copy starts as a tree alone: an eval wiki is kept without its git directory, a cut holds its
wiki as a bare tree, and a wiki no librarian built is nothing else. So its first
dreaming run commits the tree as it is before anything else, with what its source
(`CopySource`) says of it: the version of the librarian whose runs built it, if one did, and a
cut's cursor. The dreaming commit carries those forward as it does in the live wiki. The git
directory then stays beside the copy, so a later dreaming run of the same copy finds the
earlier ones in its history.

The run itself is the live wiki's (`pa_core.librarian.dreaming.run_dreaming`), told the copy's
as-of time as now, so it does to a copy whatever it does to the live wiki: a page it retires
is removed from the copy, and its slug listed in the copy's `retired.md` under the as-of day
(`pa_core.librarian.retirement`). Each run that had candidates to judge, committed or failed,
is appended to the copy's run log (`pa_core.librarian.replay.DreamingLogEntry`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.librarian.dreaming import (
    DREAMING_SETTINGS,
    DreamingOutcome,
    DreamingSettings,
    run_dreaming,
)
from pa_core.librarian.replay import (
    DreamingLogEntry,
    RunHistory,
    append_to_run_log,
    logged_sessions,
    run_cost,
    run_history,
)
from pa_core.librarian.run import (
    CURSOR_FIELD,
    VERSION_FIELD,
    Librarian,
    WikiRepository,
    standing_runs,
)
from pa_core.librarian.session import (
    LibrarianRunError,
    SessionRunner,
    fields_paragraph,
    utc,
)
from pa_core.owner import Owner


@dataclass(frozen=True)
class CopySource:
    """What a copy is a copy of, as far as dreaming it goes."""

    as_of: datetime
    """The copy's as-of time: the dreaming session is told it is now."""
    librarian_version: str | None
    """The version of the librarian whose runs built the wiki; None when nothing names it."""
    cursor: str | None = None
    """Where that librarian had got to, for a cut's wiki that says."""


class DreamCopyError(PaError):
    """A directory that isn't a copy dreaming can run over."""


def replayed_source(run_log: Path) -> CopySource:
    """What the replay whose run log is at `run_log` says of a copy of its target: its as-of
    time is the end of the replay's last run, which that run's sessions were told is now.

    Refuses (RunLogError) a log that isn't a finished replay's of one librarian version, with
    any dreaming runs after it (`run_history`), and (DreamCopyError) one with no replay run.
    """
    history = run_history(run_log)
    if not history.replays:
        raise DreamCopyError(
            f"{run_log} has no replay run: it isn't the run log of a copy of a replay target"
        )
    last = history.replays[-1]
    source = CopySource(as_of=last.occurred_to, librarian_version=last.librarian_version)
    _require_dreamed_as(history, source, run_log)
    return source


def stated_source(
    run_log: Path,
    *,
    as_of: datetime,
    librarian_version: str | None = None,
    cursor: str | None = None,
) -> CopySource:
    """What is stated of a copy that no replay's run log describes, whose own run log, once it
    has been dreamed, is at `run_log`. Such a copy is one of two things:

    - a copy of a cut's wiki: `as_of` is the cut's as-of time, and `librarian_version` and
      `cursor` are those of the wiki commit it holds;
    - a copy of a wiki no librarian built, such as one a person wrote over a corpus: `as_of`
      is the time given for it, and it has no librarian version and no cursor.

    Refuses (DreamCopyError) a run log with a replay's runs, which is a copy of a replay
    target's, and one whose dreaming runs were as of another time or of a wiki another
    librarian version built: a copy of another wiki.
    """
    source = CopySource(as_of=as_of, librarian_version=librarian_version, cursor=cursor)
    if run_log.is_file():
        history = run_history(run_log)
        if history.replays:
            raise DreamCopyError(
                f"{run_log} has a replay's runs: it is the run log of a copy of a replay "
                "target, whose as-of time is its replay's, not one stated for it"
            )
        _require_dreamed_as(history, source, run_log)
    return source


def _require_dreamed_as(history: RunHistory, source: CopySource, run_log: Path) -> None:
    """Refuses a copy whose earlier dreaming runs weren't of `source`'s wiki
    (`RunHistory.dreamed_otherwise`): one wiki is dreamed as of one time."""
    other = history.dreamed_otherwise(
        as_of=source.as_of, librarian_version=source.librarian_version
    )
    if other is None:
        return
    not_this = "the copy is of another wiki than the one it's being dreamed as"
    if other.as_of != source.as_of:
        raise DreamCopyError(
            f"{run_log} has a dreaming run as of {utc(other.as_of)}, "
            f"and this one would be as of {utc(source.as_of)}: {not_this}"
        )
    raise DreamCopyError(
        f"{run_log} has a dreaming run of a wiki {_built(other.librarian_version)}, and this "
        f"one would be of a wiki {_built(source.librarian_version)}: {not_this}"
    )


def _built(librarian_version: str | None) -> str:
    """How a refusal says who built a wiki: a librarian version, or none."""
    if librarian_version is None:
        return "no librarian built"
    return f"built by librarian version {librarian_version}"


COPY_AS_OF_FIELD = "Copy-As-Of"
"""The field of a copy's first commit holding its as-of time, UTC ISO 8601."""


def run_copy_dreaming(
    *,
    store: L0Store,
    owner: Owner,
    time_zone: tzinfo,
    repository: WikiRepository,
    runner: SessionRunner,
    librarian: Librarian,
    run_log: Path,
    source: CopySource,
    settings: DreamingSettings = DREAMING_SETTINGS,
) -> DreamingOutcome:
    """Runs dreaming once over the copy in `repository`, a copy of `source`, at its as-of
    time in `time_zone`, and appends the run to `run_log`.

    `store` is the L0 the copy's wiki was built over. The `settings` are a dreaming run's,
    and so is its memory: the copy's own earlier dreaming runs, all as of the same
    time, so what one dismissed is held back from the next, and what the cap left over is
    offered. With no candidates it logs nothing. Raises LibrarianRunError when the run fails,
    after logging it; the copy is then as last committed.
    """
    _start(repository, source)
    try:
        outcome = run_dreaming(
            store=store,
            owner=owner,
            repository=repository,
            runner=runner,
            librarian=librarian,
            now=source.as_of.astimezone(time_zone),
            settings=settings,
        )
    except LibrarianRunError as error:
        append_to_run_log(run_log, _entry(source, librarian, error=error))
        raise
    if outcome.commit is not None:
        append_to_run_log(run_log, _entry(source, librarian, outcome=outcome))
    return outcome


def _start(repository: WikiRepository, source: CopySource) -> None:
    """Commits the copy's tree as it is, unless its history already says where it's from: a
    dreaming run starts by restoring the last commit, which must hold the copy."""
    if standing_runs(repository):
        return
    fields = [
        (CURSOR_FIELD, source.cursor),
        (VERSION_FIELD, source.librarian_version),
        (COPY_AS_OF_FIELD, utc(source.as_of)),
    ]
    listed = fields_paragraph((key, value) for key, value in fields if value is not None)
    repository.commit(f"Start the copy to dream\n\n{listed}\n")


def _entry(
    source: CopySource,
    librarian: Librarian,
    *,
    outcome: DreamingOutcome | None = None,
    error: LibrarianRunError | None = None,
) -> DreamingLogEntry:
    """The run log's entry for a dreaming run of a copy of `source`: committed with `outcome`,
    or failed with `error`."""
    sessions = outcome.sessions if outcome else error.sessions if error else ()
    return DreamingLogEntry(
        run="dreaming",
        status="committed" if outcome else "failed",
        commit=outcome.commit if outcome else None,
        as_of=source.as_of,
        librarian_version=source.librarian_version,
        dreaming_version=librarian.version,
        model=librarian.model,
        effort=librarian.effort,
        sessions=logged_sessions(sessions),
        cost_usd=run_cost(sessions, committed=outcome is not None),
        candidates=len(outcome.candidates) if outcome else None,
        outcomes=outcome.counts if outcome else None,
        lint_warnings=len(outcome.warnings) if outcome else None,
        error=str(error) if error else None,
    )
