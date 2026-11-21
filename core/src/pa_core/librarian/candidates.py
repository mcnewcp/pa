"""Candidates: the problems the host flags mechanically for dreaming to judge (spec #140).

The model's judgment is spent on judging, not on finding: before a dreaming run the host lists
what looks wrong with the wiki, and the session gives each candidate one outcome
(`pa_core.librarian.dreaming`). `candidates` is the one entry point, and nothing else knows
which problems are offered, in what order, how many, or what each is called.

A candidate is one of two things:

- **A lint warning**: an orphan page, a loop past due or open too long, a duplicate candidate,
  an engaged slug that reached the page threshold without a page, or a "needs lookup" marker.
- **A problem lint has no warning for**, since only dreaming acts on it. These are found
  here, against lint's thresholds:
  - a stale page, which observations have linked on enough days since it was last updated;
  - a status that disagrees with its loops: a page that says its matter is over (a settled
    or dormant topic, an inactive person or organization) while one of its loops is open;
  - a quiet page: an active one that no observation has linked for a long time.

They are offered in a fixed order of kinds (`ORDER`), which matters because a run is offered
only so many (`CANDIDATE_CAP`, unless configured) and the rest wait for the next run:

1. Stale pages. Every later judgment about a page reads it, so it is brought up to date
   first, and a page left behind is the failure that costs a wrong answer.
2. Statuses that disagree with their loops, ahead of the loops themselves: settling one often
   closes the loop.
3. Lint's warnings, in lint's order.
4. Quiet pages. Whether one may be marked dormant or inactive depends on its loops, which are
   judged before it, and leaving one for the next run costs only the index's order.

Days after the run's own date never count. Day logs exist for them wherever a calendar event
is booked ahead, and what hasn't happened yet has left no page behind.

Dreaming remembers what it has judged, so each run is spent on what's new. The memory is the
dreaming runs' own commits (`DreamingHistory`, which `pa_core.librarian.dreaming` reads back
from the wiki's history), and a candidate is the same one from run to run by its identity:
its kind, its pages, and for a loop the episodes its line cites. Two things hold one back:

- **It was dismissed**, until one of its pages changes or `DISMISSAL_DAYS` pass.
- **It was proposed**, while that proposal is open.

An applied candidate is never held back: if the problem is still there, it is offered again.
Two loops on one page that cite the same episodes are one identity, so they are held back
together.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path

from pa_core.librarian.lint import Finding, L0Metadata, Thresholds, lint
from pa_core.librarian.wiki import (
    ACTIVE,
    DAYS,
    PAGE_FOLDERS,
    Page,
    linked_days,
    open_proposals,
    pages,
)
from pa_core.owner import Owner


class CandidateKind(StrEnum):
    """What a candidate is about. `ORDER` is the order `candidates` offers the kinds in, and
    the librarian project's dreaming instructions say how to judge each."""

    STALE = "stale"
    """A page that observations have linked on enough days since it was last updated."""
    STATUS_MISMATCH = "status-mismatch"
    """A page whose status says its matter is over while one of its loops is open."""
    ORPHAN = "orphan"
    """A page nothing in the wiki links to."""
    LOOP_PAST_DUE = "loop-past-due"
    LOOP_AGING = "loop-aging"
    """A loop open longer than the loop age, and not past due."""
    DUPLICATE = "duplicate"
    """Two pages that may be one."""
    SLUG_WITHOUT_PAGE = "slug-without-page"
    NEEDS_LOOKUP = "needs-lookup"
    QUIET = "quiet"
    """An active page no observation has linked for a long time."""


@dataclass(frozen=True)
class Candidate:
    """One problem offered to a dreaming run."""

    id: str
    """What the run's outcomes call it: `c01`, `c02`, and so on, in the order offered."""
    kind: CandidateKind
    pages: tuple[str, ...]
    """The pages it's about, relative to the wiki: both pages of a duplicate, a page whose
    status disagrees with its loops and then every other page holding one of them, and none
    for a slug without a page."""
    path: str
    """The file the problem is in, relative to the wiki: one of `pages`, or a day log."""
    message: str
    """The problem, as lint words it, or as this module does."""
    line: int | None = None
    """The line in `path`, counting from 1, when the problem is on one."""
    episodes: tuple[str, ...] = ()
    """For a loop, the episodes its line cites as opening it: which loop it is, wherever its
    line moves."""
    slug: str | None = None
    """For a slug without a page, the slug."""
    day_logs: tuple[str, ...] = ()
    """For a stale page, the day logs that link it since its last update, relative to the
    wiki, oldest first."""

    @property
    def subject(self) -> str:
        """What it's about, in a line: its pages (with a loop's episodes), or its slug, or the
        file it's in."""
        if self.pages:
            cited = "".join(f" [ep:{episode}]" for episode in self.episodes)
            return ", ".join(self.pages) + cited
        return f"[[{self.slug}]]" if self.slug else self.path

    @property
    def identity(self) -> Identity:
        """Which candidate it is, from run to run: its kind, its pages, and for a loop the
        episodes its line cites (or its slug, or its file). Its id is only its place in one
        run."""
        return _identity(str(self.kind), self.subject)

    def to_json(self) -> dict[str, object]:
        """The candidate as a dreaming session is given it."""
        listed: dict[str, object] = {
            "id": self.id,
            "kind": str(self.kind),
            "pages": list(self.pages),
            "path": self.path,
        }
        if self.line is not None:
            listed["line"] = self.line
        listed["message"] = self.message
        if self.episodes:
            listed["episodes"] = list(self.episodes)
        if self.slug is not None:
            listed["slug"] = self.slug
        if self.day_logs:
            listed["day_logs"] = list(self.day_logs)
        return listed


@dataclass(frozen=True)
class Decision:
    """What an earlier dreaming run decided about one candidate, as its commit lists it."""

    kind: str
    subject: str
    """What the candidate was about, as `Candidate.subject` wrote it."""
    outcome: str
    """`applied`, `proposed`, or `dismissed`."""
    proposal: str | None = None
    """For a proposed candidate, the id of the proposal that asks about it."""


@dataclass(frozen=True)
class PastRun:
    """An earlier dreaming run whose commit still stands."""

    age: timedelta
    """How long ago it ran: from the time it was told was now to the time this run is told."""
    decisions: tuple[Decision, ...]
    changed: frozenset[str] = frozenset()
    """The files, relative to the wiki, whose content now differs from what the run
    committed."""


@dataclass(frozen=True)
class DreamingHistory:
    """What dreaming remembers: its earlier runs that still stand, newest first. A wiki that
    was never dreamed, or has no history at all, has none."""

    runs: tuple[PastRun, ...] = ()


NO_HISTORY = DreamingHistory()
CANDIDATE_CAP = 30
"""The most candidates one run is offered, unless configured otherwise, so that a first run
over a wiki full of them stays inside its session's turn limit."""
DISMISSAL_DAYS = 30
"""How long a dismissed candidate is held back while its pages stay as they were."""


def candidates(
    tree: Path,
    *,
    l0: L0Metadata,
    owner: Owner,
    thresholds: Thresholds,
    history: DreamingHistory = NO_HISTORY,
    cap: int = CANDIDATE_CAP,
) -> tuple[Candidate, ...]:
    """The candidates to offer a dreaming run over the wiki at `tree`: what is flagged in it,
    measured against `l0`'s metadata and `thresholds` for `owner`, in the order of `ORDER`,
    less what `history` holds back, and no more than `cap` of them. The rest wait for the next
    run, which this run's outcomes make room for.

    A candidate that `history` shows an earlier run dismissed is held back until one of its
    pages changes or `DISMISSAL_DAYS` pass, and one an earlier run proposed, while that
    proposal is open in the wiki.

    The ids follow from the wiki and the history alone, so the same wiki on the same day
    offers the same candidates under the same ids.
    """
    warnings = lint(tree, l0=l0, owner=owner, thresholds=thresholds).warnings
    found = pages(tree)
    linked = _linked_days(tree, thresholds.today)
    flagged = [
        *_stale(found, linked, thresholds),
        *_status_mismatches(found),
        *(_of_warning(warning) for warning in warnings),
        *_quiet(found, linked, thresholds),
    ]
    held = _held_back(history, {proposal.id for proposal in open_proposals(tree)})
    # The sort is stable: within a row of `ORDER`, candidates stay as they were found.
    offered = sorted(
        (candidate for candidate in flagged if candidate.identity not in held),
        key=lambda candidate: _RANK[candidate.kind],
    )[:cap]
    return tuple(
        replace(candidate, id=f"c{number:02d}") for number, candidate in enumerate(offered, start=1)
    )


ORDER: tuple[tuple[CandidateKind, ...], ...] = (
    (CandidateKind.STALE,),
    (CandidateKind.STATUS_MISMATCH,),
    (CandidateKind.ORPHAN,),
    # A page's loops come together, past due or aging, page by page.
    (CandidateKind.LOOP_PAST_DUE, CandidateKind.LOOP_AGING),
    (CandidateKind.DUPLICATE,),
    (CandidateKind.SLUG_WITHOUT_PAGE,),
    (CandidateKind.NEEDS_LOOKUP,),
    (CandidateKind.QUIET,),
)
"""The fixed order the kinds are offered in, one row per place: what a capped run reaches
first. The module's docstring gives the reasons. A new kind is a row here."""
_RANK = {kind: rank for rank, row in enumerate(ORDER) for kind in row}


type Identity = tuple[str, frozenset[str]]
"""Which candidate one is, from run to run: its kind and what it's about."""


def _identity(kind: str, subject: str) -> Identity:
    """A candidate's identity, from its kind and its subject: the subject's parts (its pages
    and a loop's citations, or its slug, or its file) in any order. No part holds a space or a
    comma."""
    return kind, frozenset(subject.replace(",", " ").split())


def _is_page(path: str) -> bool:
    return path.partition("/")[0] in PAGE_FOLDERS


def _held_back(history: DreamingHistory, asked: Collection[str]) -> set[Identity]:
    """The candidates not to offer again yet, given the ids of the proposals `asked` of the
    owner and still open. What counts is the newest run that judged a candidate; any older
    decision about it is superseded.

    A proposed candidate is held back while its proposal is open, so the owner isn't asked the
    same thing twice. Once it is answered, the candidate is offered again if the problem
    remains.

    A dismissed candidate is held back for `DISMISSAL_DAYS`, or until one of its pages
    changes: until a page's content differs from what the run that dismissed it committed,
    whoever changed it (a nightly run, a later dreaming run, the host rewriting a link in it,
    the owner by hand). A candidate about no page (a slug without one, a marker in a day log)
    waits out the days.
    """
    held: set[Identity] = set()
    judged: set[Identity] = set()
    for run in history.runs:
        decided = {_identity(decision.kind, decision.subject) for decision in run.decisions}
        for decision in run.decisions:
            identity = _identity(decision.kind, decision.subject)
            if identity in judged:
                continue
            if decision.outcome == "proposed":
                waits = decision.proposal in asked
            else:
                waits = (
                    decision.outcome == "dismissed"
                    and run.age < timedelta(days=DISMISSAL_DAYS)
                    and not any(_is_page(path) for path in run.changed & identity[1])
                )
            if waits:
                held.add(identity)
        judged |= decided
    return held


_UNNUMBERED = ""
"""A candidate's id until `candidates` numbers it."""


def _of_warning(warning: Finding) -> Candidate:
    if warning.kind is None:
        raise ValueError(f"a lint warning without a kind can't be a candidate: {warning}")
    return Candidate(
        id=_UNNUMBERED,
        kind=CandidateKind(warning.kind.value),
        pages=warning.pages,
        path=warning.path,
        message=warning.message,
        line=warning.line,
        episodes=warning.episodes,
        slug=warning.slug,
    )


def _linked_days(tree: Path, today: date) -> dict[str, list[date]]:
    """For each slug, the days up to `today` that an observation links it on, in order."""
    return {slug: [day for day in days if day <= today] for slug, days in linked_days(tree).items()}


def _stale(
    pages: Sequence[Page], linked: Mapping[str, Sequence[date]], thresholds: Thresholds
) -> Iterator[Candidate]:
    """A page whose slug observations link on enough days after its `updated` date: what it
    is about moved on, and the page didn't."""
    for page in pages:
        updated = _updated(page)
        if updated is None:
            continue
        later = [day for day in linked.get(page.slug, ()) if day > updated]
        if len(later) >= thresholds.stale_later_days:
            yield Candidate(
                id=_UNNUMBERED,
                kind=CandidateKind.STALE,
                pages=(page.path,),
                path=page.path,
                message=f"a stale page: updated {updated}, and observations link "
                f"[[{page.slug}]] on {_count(len(later), 'later day')}",
                day_logs=tuple(f"{DAYS}/{day.isoformat()}.md" for day in later),
            )


def _status_mismatches(pages: Sequence[Page]) -> Iterator[Candidate]:
    """A page that isn't active while a loop of its is open: a loop on the page itself, or a
    loop on another page that names it as a party. Every status but `active` says the matter
    is over, for good or for now (`settled`, `dormant`, `inactive`), so either the status is
    wrong or the loop is no longer owed; L0 says which."""
    holding: dict[str, list[str]] = defaultdict(list)
    """For each slug, the page holding each open loop of its, once per loop."""
    for page in pages:
        for loop in page.loops:
            if loop.is_open:
                for slug in {page.slug, loop.owed_by, loop.owed_to}:
                    holding[slug].append(page.path)
    for page in pages:
        status = page.fields.get("status")
        held = holding.get(page.slug)
        if not held or status == ACTIVE or status not in page.page_type.statuses:
            continue
        where = tuple(dict.fromkeys(held))
        yield Candidate(
            id=_UNNUMBERED,
            kind=CandidateKind.STATUS_MISMATCH,
            pages=(page.path, *(path for path in where if path != page.path)),
            path=page.path,
            message=f"a status that disagrees with its loops: it is {status}, with "
            f"{_count(len(held), 'open loop')} on {', '.join(where)}",
        )


def _quiet(
    pages: Sequence[Page], linked: Mapping[str, Sequence[date]], thresholds: Thresholds
) -> Iterator[Candidate]:
    """An active page whose slug no observation has linked for the quiet threshold's days. A
    page no observation has linked yet has nothing to measure from, and isn't one."""
    for page in pages:
        days = linked.get(page.slug)
        if page.fields.get("status") != ACTIVE or not days:
            continue
        quiet = (thresholds.today - days[-1]).days
        if quiet >= thresholds.quiet_days:
            yield Candidate(
                id=_UNNUMBERED,
                kind=CandidateKind.QUIET,
                pages=(page.path,),
                path=page.path,
                message="a quiet page: it is active, and no observation has linked "
                f"[[{page.slug}]] since {days[-1]}, {_count(quiet, 'day')} ago",
            )


def _updated(page: Page) -> date | None:
    """The day a page says it last changed; None when it doesn't say (lint reports that)."""
    value = page.fields.get("updated")
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
