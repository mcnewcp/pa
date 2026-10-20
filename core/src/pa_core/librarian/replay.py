"""A replay: L0 run through the librarian a window (a week) at a time, into a fresh wiki.

It rebuilds a wiki (principle 2), and it builds each eval wiki, once per librarian version. Each
window of occurrence is one full run (`run_librarian`), told the window's end as now, and
committed with the window in its message. Windows run from the Monday of the first episode's
week up to the as-of time, or else up to the latest episode; the last one also takes any episode
after it. A window with no episodes has no run.

A replay resumes from its last committed run: the next window starts where that one ended.

The run log has one entry per run, committed or failed, appended as the run ends: a JSON object
per line, with these keys in this order.

- `status`: `committed` or `failed`.
- `commit`: the run's commit, or null when it failed.
- `occurred_from`, `occurred_to`: the batch's window, UTC ISO 8601. The end is what the
  sessions were told is now.
- `episodes`: how many episodes were offered; `skipped`: how many of the window's weren't.
- `librarian_version`, `model`, `effort`: the librarian that ran.
- `sessions`: `{phase, session_id, cost_usd, turns}` for each session, in order (empty when the
  run failed or had nothing to offer).
- `cost_usd`: the sessions' total, or null if one didn't report its cost.
- `error`: why the run failed, or null.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path

from pa_core.l0 import L0Store
from pa_core.librarian.batch import Batch, Window, episode_day, replay_batch
from pa_core.librarian.run import (
    PAGE_THRESHOLD,
    Librarian,
    LibrarianRunError,
    RunOutcome,
    SessionRunner,
    WikiRepository,
    _fields,
    run_librarian,
)
from pa_core.owner import Owner

BATCH_DAYS = 7
"""A replay batch's length in days, unless given otherwise."""

_TO, _VERSION = "Batch-Occurred-To", "Librarian-Version"


class ReplayError(LibrarianRunError):
    """A replay that can't start or resume."""


@dataclass(frozen=True)
class ReplayOutcome:
    """The runs a replay committed, in order (none when it had already finished)."""

    runs: tuple[RunOutcome, ...]


def replay_librarian(
    *,
    store: L0Store,
    owner: Owner,
    time_zone: tzinfo,
    repository: WikiRepository,
    runner: SessionRunner,
    librarian: Librarian,
    run_log: Path,
    batch_days: int = BATCH_DAYS,
    as_of: datetime | None = None,
    page_threshold: int = PAGE_THRESHOLD,
    progress: Callable[[RunOutcome], None] = lambda _: None,
) -> ReplayOutcome:
    """Replays L0 through the librarian into `repository`, `batch_days` days at a time.

    Starts after the repository's last committed replay run, or from the beginning in a fresh
    wiki; refuses a wiki that a production run or another librarian version committed to.
    Appends an entry to `run_log` for every run, and calls `progress` after each committed one.
    Raises LibrarianRunError when a run fails, after logging it; the wiki is then as of the
    last committed run, ready to resume.
    """
    if batch_days < 1:
        raise ReplayError(f"a replay batch must be at least 1 day, not {batch_days}")
    resume_from = _resume_point(repository, librarian)
    runs: list[RunOutcome] = []
    for window, last in _windows(store, time_zone, batch_days, resume_from, as_of):
        batch = replay_batch(store, window, time_zone=time_zone, last=last)
        if batch is None:
            continue
        try:
            outcome = run_librarian(
                store=store,
                owner=owner,
                time_zone=time_zone,
                repository=repository,
                runner=runner,
                librarian=librarian,
                now=window.end.astimezone(time_zone),
                page_threshold=page_threshold,
                batch=batch,
            )
        except LibrarianRunError as error:
            _log(run_log, _entry(batch, librarian, error=str(error)))
            raise
        _log(run_log, _entry(batch, librarian, outcome=outcome))
        runs.append(outcome)
        progress(outcome)
    return ReplayOutcome(tuple(runs))


def _resume_point(repository: WikiRepository, librarian: Librarian) -> datetime | None:
    """Where the last committed replay run's window ended, or None in a fresh wiki."""
    fields = _fields(repository.last_commit_message() or "")
    if _TO not in fields:
        if fields:
            raise ReplayError(
                "the wiki's last commit isn't a replay's run, so a replay can't resume it"
            )
        return None
    if fields.get(_VERSION) != librarian.version:
        raise ReplayError(
            f"the wiki was replayed by librarian version {fields.get(_VERSION)}, not this one "
            f"({librarian.version}); replay into a fresh wiki instead"
        )
    return datetime.fromisoformat(fields[_TO])


def _windows(
    store: L0Store,
    time_zone: tzinfo,
    batch_days: int,
    resume_from: datetime | None,
    as_of: datetime | None,
) -> Iterator[tuple[Window, bool]]:
    """Each window to replay, and whether it's the last."""
    days = [episode_day(e, time_zone) for e in store.envelopes()]
    if not days:
        return
    first, latest = min(days), max(days)
    start = resume_from or _midnight(first - timedelta(days=first.weekday()), time_zone)
    length = timedelta(days=batch_days)
    while as_of is None or start < as_of:
        start_day = start.astimezone(time_zone).date()
        if as_of is None and start_day > latest:
            return
        end = _midnight(start_day + length, time_zone)
        last = end >= as_of if as_of is not None else start_day + length > latest
        yield Window(start, min(end, as_of) if as_of is not None else end), last
        if last:
            return
        start = end


def _midnight(day: date, time_zone: tzinfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=time_zone)


def _entry(
    batch: Batch,
    librarian: Librarian,
    *,
    outcome: RunOutcome | None = None,
    error: str | None = None,
) -> dict[str, object]:
    """The run log's entry for a run of `batch`: committed with `outcome`, or failed."""
    assert batch.window is not None
    sessions = outcome.sessions if outcome else ()
    costs = [result.cost_usd for _, result in sessions]
    return {
        "status": "committed" if outcome else "failed",
        "commit": outcome.commit if outcome else None,
        "occurred_from": batch.window.start.astimezone(UTC).isoformat(),
        "occurred_to": batch.window.end.astimezone(UTC).isoformat(),
        "episodes": len(batch.episodes),
        "skipped": batch.captured - len(batch.episodes),
        "librarian_version": librarian.version,
        "model": librarian.model,
        "effort": librarian.effort,
        "sessions": [
            {
                "phase": str(phase),
                "session_id": result.session_id,
                "cost_usd": result.cost_usd,
                "turns": result.turns,
            }
            for phase, result in sessions
        ],
        "cost_usd": None if None in costs else round(sum(c or 0 for c in costs), 6),
        "error": error,
    }


def _log(run_log: Path, entry: dict[str, object]) -> None:
    run_log.parent.mkdir(parents=True, exist_ok=True)
    with run_log.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
