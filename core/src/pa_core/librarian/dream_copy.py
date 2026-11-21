"""Dreaming a copy: a dreaming run over a copy of a replay target, or of a cut's wiki, at the
copy's as-of time (spec #140).

It is how dreaming's effect is measured: the eval harness asks the same questions with a wiki
and with a dreamed copy of it. The copy is laid out as a replay target is
(`pa_core.librarian.replay.ReplayTarget`): the wiki's tree, its git directory, and the run log.

A copy starts as a tree alone: an eval wiki is kept without its git directory, and a cut holds
its wiki as a bare tree. So its first dreaming run commits the tree as it is before anything
else, with what its source (`CopySource`) says of it: the version of the librarian whose runs
built it, and a cut's cursor. The dreaming commit carries those forward as it does in the live
wiki. The git directory then stays beside the copy, so a later dreaming run of the same copy
finds the earlier ones in its history.

The run itself is the live wiki's (`pa_core.librarian.dreaming.run_dreaming`), told the copy's
as-of time as now. Each run that had candidates to judge, committed or failed, is appended to
the copy's run log (`pa_core.librarian.replay.DreamingLogEntry`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.librarian.dreaming import (
    OUTCOMES,
    PROPOSAL_CAPS,
    DreamingOutcome,
    ProposalCaps,
    run_dreaming,
)
from pa_core.librarian.lint import QUIET_DAYS, STALE_LATER_DAYS
from pa_core.librarian.replay import (
    DreamingLogEntry,
    RunHistory,
    RunLogSession,
    append_to_run_log,
    run_history,
)
from pa_core.librarian.run import (
    CURSOR_FIELD,
    LOOP_AGE_DAYS,
    PAGE_THRESHOLD,
    VERSION_FIELD,
    Librarian,
    LibrarianRunError,
    SessionRunner,
    WikiRepository,
    commit_fields,
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
    _require_dreamed_as_of(history, source, run_log)
    return source


def cut_source(
    run_log: Path, *, as_of: datetime, librarian_version: str | None, cursor: str | None
) -> CopySource:
    """What a cut says of a copy of its wiki, whose run log, once it has been dreamed, is at
    `run_log`: the cut's as-of time, and the librarian version and cursor of the wiki commit
    it holds.

    Refuses (DreamCopyError) a run log with a replay's runs, which is a copy of a replay
    target's, and one whose dreaming runs were as of another time: a copy of another cut's
    wiki.
    """
    source = CopySource(as_of=as_of, librarian_version=librarian_version, cursor=cursor)
    if run_log.is_file():
        history = run_history(run_log)
        if history.replays:
            raise DreamCopyError(
                f"{run_log} has a replay's runs: it is the run log of a copy of a replay "
                "target, not of a cut's wiki"
            )
        _require_dreamed_as_of(history, source, run_log)
    return source


def _require_dreamed_as_of(history: RunHistory, source: CopySource, run_log: Path) -> None:
    """Refuses a copy whose earlier dreaming runs were told another time than `source`'s
    as-of time: one wiki is dreamed as of one time."""
    for entry in history.dreams:
        if entry.as_of != source.as_of:
            raise DreamCopyError(
                f"{run_log} has a dreaming run as of {entry.as_of.astimezone(UTC).isoformat()}, "
                f"and this one would be as of {source.as_of.astimezone(UTC).isoformat()}: the "
                "copy is of another wiki than the one it's being dreamed as"
            )


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
    page_threshold: int = PAGE_THRESHOLD,
    loop_age_days: int = LOOP_AGE_DAYS,
    stale_later_days: int = STALE_LATER_DAYS,
    quiet_days: int = QUIET_DAYS,
    proposal_caps: ProposalCaps = PROPOSAL_CAPS,
) -> DreamingOutcome:
    """Runs dreaming once over the copy in `repository`, a copy of `source`, at its as-of
    time in `time_zone`, and appends the run to `run_log`.

    `store` is the L0 the copy's wiki was built over. The thresholds and caps are a dreaming
    run's. With no candidates it logs nothing. Raises LibrarianRunError when the run fails,
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
            page_threshold=page_threshold,
            loop_age_days=loop_age_days,
            stale_later_days=stale_later_days,
            quiet_days=quiet_days,
            proposal_caps=proposal_caps,
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
    if commit_fields(repository.last_commit_message() or ""):
        return
    fields = [
        (CURSOR_FIELD, source.cursor),
        (VERSION_FIELD, source.librarian_version),
        (COPY_AS_OF_FIELD, source.as_of.astimezone(UTC).isoformat()),
    ]
    listed = "\n".join(f"{key}: {value}" for key, value in fields if value is not None)
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
    costs = [result.cost_usd for _, result in sessions]
    known = [cost for cost in costs if cost is not None]
    if outcome is not None:
        cost = None if None in costs else round(sum(known), 6)
    else:
        # What a failed run cost is what its sessions reported, never a made-up zero.
        cost = round(sum(known), 6) if known else None
    return DreamingLogEntry(
        run="dreaming",
        status="committed" if outcome else "failed",
        commit=outcome.commit if outcome else None,
        as_of=source.as_of,
        librarian_version=source.librarian_version,
        dreaming_version=librarian.version,
        model=librarian.model,
        effort=librarian.effort,
        sessions=tuple(
            RunLogSession(
                phase=phase,
                session_id=result.session_id,
                cost_usd=result.cost_usd,
                turns=result.turns,
                resumes=result.resumes,
            )
            for phase, result in sessions
        ),
        cost_usd=cost,
        candidates=len(outcome.candidates) if outcome else None,
        outcomes={name: outcome.count(name) for name in OUTCOMES} if outcome else None,
        lint_warnings=len(outcome.warnings) if outcome else None,
        error=str(error) if error else None,
    )
