"""Candidates: what the host flags mechanically for dreaming to judge (spec #140).

The model's judgment is spent on judging, not on finding: before a dreaming run the host lists
what looks wrong with the wiki, and the session gives each candidate one outcome
(`pa_core.librarian.dreaming`). `candidates` is the one entry point, and nothing else knows
what is flagged, in what order it is offered, how much of it, or what each is called. It returns an
`Offer`: the candidates a run is offered, and the ones it isn't, held back by dreaming's
memory or left over the cap.

A candidate is a question, not a finding: dreaming decides whether it is a problem. It is
one of two things:

- **A lint warning**: an orphan page, a loop past due or open too long, a duplicate candidate,
  an engaged slug that reached the page threshold without a page, or a "needs lookup" marker.
- **Something lint has no warning for**, since only dreaming acts on it. These are found
  here, against lint's thresholds:
  - a stale page, which observations have linked on enough days since it was last updated;
  - a status that disagrees with its loops: a page that says its matter is over (a settled
    or dormant topic, an inactive person or organization) while one of its loops is open;
  - a quiet page: an active one that no observation has linked for a long time;
  - an unengaged page: a person's or an organization's that the owner has never had a hand in
    (`pa_core.librarian.engagement`), which dreaming may retire.

They are offered in a fixed order of kinds (`ORDER`), which matters because a run is offered
only so many (`CANDIDATE_CAP`, unless configured) and the rest wait for the next run:

1. Stale pages. Every later judgment about a page reads it, so it is brought up to date
   first, and a page left behind is the failure that costs a wrong answer.
2. Statuses that disagree with their loops, ahead of the loops themselves: settling one often
   closes the loop.
3. Lint's warnings, in lint's order.
4. Quiet pages. Whether one may be marked dormant or inactive depends on its loops, which are
   judged before it, and leaving one for the next run costs only the index's order.
5. Unengaged pages. Whether one may be retired depends on its loops and on whether it is
   another page's duplicate, all judged before it.

Days after the run's own date never count. Day logs exist for them wherever a calendar event
is booked ahead, and what hasn't happened yet has left no page behind.

Dreaming remembers what it has judged, so each run is spent on what's new. The memory is the
dreaming runs' own commits (`DreamingHistory`, which `pa_core.librarian.dreaming` reads back
from the wiki's history), and a candidate is the same one from run to run by its identity:
its kind, its pages, and for a loop the episodes its line cites. Two things hold one back:

- **It was dismissed**, until one of its pages changes or `DISMISSAL_DAYS` pass. Not when it
  was dismissed only because the run had no proposal left to give it: it deserved a question,
  so the next run is offered it again.
- **It was proposed**, while that proposal is open.

An applied candidate is never held back: if it is still flagged, it is offered again.
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

from pa_core.librarian.engagement import engaged_days
from pa_core.librarian.kinds import CandidateKind
from pa_core.librarian.lint import Finding, L0Metadata, Thresholds, lint
from pa_core.librarian.wiki import (
    ACTIVE,
    DAYS,
    ORGANIZATION,
    PAGE_FOLDERS,
    PERSON,
    Page,
    citations,
    linked_days,
    links,
    open_proposals,
    pages,
)
from pa_core.owner import Owner


@dataclass(frozen=True)
class Candidate:
    """One thing the host flagged in the wiki, offered to a dreaming run to judge."""

    id: str
    """What the run's outcomes call it: `c01`, `c02`, and so on, in the order offered."""
    kind: CandidateKind
    pages: tuple[str, ...]
    """The pages it's about, relative to the wiki: both pages of a duplicate, a page whose
    status disagrees with its loops and then every other page holding one of them, and none
    for a slug without a page."""
    path: str
    """The file it is flagged in, relative to the wiki: one of `pages`, or a day log."""
    message: str
    """What was flagged, as lint words it, or as this module does."""
    line: int | None = None
    """The line in `path`, counting from 1, when it is on one."""
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
        if self.pages:
            return Identity(str(self.kind), frozenset(self.pages), frozenset(self.episodes))
        if self.slug:
            return Identity(str(self.kind), slug=self.slug)
        return Identity(str(self.kind), file=self.path)

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
class Identity:
    """Which candidate one is, from run to run: its kind and what it is about, in any order.

    A dreaming commit lists each candidate by its kind and its subject (`Candidate.subject`),
    and `parse` reads the identity back from that line, so the two say the same thing in two
    forms: change one with the other, or dreaming forgets what it judged.
    """

    kind: str
    pages: frozenset[str] = frozenset()
    """The pages it is about, relative to the wiki."""
    episodes: frozenset[str] = frozenset()
    """For a loop, the episodes its line cites as opening it."""
    slug: str | None = None
    """For a slug without a page, the slug."""
    file: str | None = None
    """For a candidate about no page and no slug (a marker in a day log), the file it is in."""

    @classmethod
    def parse(cls, kind: str, subject: str) -> Identity:
        """The identity of the candidate a dreaming commit lists with `kind` and `subject`: the
        subject's parts, which are its pages and a loop's citations, or its slug as a link, or
        its file. No part holds a space or a comma."""
        parts = subject.replace(",", " ").split()
        found = {part for part in parts if part.partition("/")[0] in PAGE_FOLDERS}
        cited = {episode for part in parts for episode in citations(part)}
        linked = [slug for part in parts for slug in links(part)]
        rest = [part for part in parts if part not in found and not citations(part)]
        if found:
            return cls(kind, frozenset(found), frozenset(cited))
        if linked:
            return cls(kind, slug=linked[0])
        return cls(kind, file=" ".join(rest) or None)


class OutcomeName(StrEnum):
    """Each outcome a dreaming run can give a candidate, in the order they are counted in. A
    session reports one by its value, and a dreaming commit lists and counts them by it."""

    APPLIED = "applied"
    """L0 settled the candidate, so the session fixed the wiki."""
    PROPOSED = "proposed"
    """L0 can't settle it and the owner can, so a proposal asks."""
    DISMISSED = "dismissed"
    """It isn't a problem, or nothing can settle it; nothing changed for it."""


@dataclass(frozen=True)
class PastOutcome:
    """What an earlier dreaming run decided about one candidate, as its commit lists it."""

    identity: Identity
    """Which candidate it was (`Identity.parse`, of its line in the run's commit)."""
    outcome: OutcomeName
    proposal: str | None = None
    """For a proposed candidate, the id of the proposal that asks about it."""
    no_room: bool = False
    """For a dismissed candidate, whether it was set aside only because the run had no
    proposal left to give it."""


@dataclass(frozen=True)
class PastRun:
    """An earlier dreaming run whose commit still stands."""

    age: timedelta
    """How long ago it ran: from the time it was told was now to the time this run is told."""
    outcomes: tuple[PastOutcome, ...]
    changed: frozenset[str] = frozenset()
    """The files, relative to the wiki, whose content now differs from what the run
    committed."""


@dataclass(frozen=True)
class DreamingHistory:
    """What dreaming remembers: its earlier runs that still stand, newest first. A wiki that
    was never dreamed, or has no history at all, has none."""

    runs: tuple[PastRun, ...] = ()


@dataclass(frozen=True)
class Offer:
    """What the wiki holds for one dreaming run: every candidate flagged in it, as the ones
    the run is offered and the ones it isn't."""

    offered: tuple[Candidate, ...]
    """The candidates the run is offered, numbered in the order offered."""
    held_back: tuple[Candidate, ...] = ()
    """The candidates history holds back: what an earlier run dismissed, or asked the owner
    about in a proposal that is still open. A wiki whose every candidate is held back has
    nothing to judge, which isn't the same as having nothing flagged."""
    left_over: tuple[Candidate, ...] = ()
    """The candidates past the cap, in order: they wait for the next run, which this run's
    outcomes make room for."""

    @property
    def flagged(self) -> tuple[Candidate, ...]:
        """Every candidate flagged in the wiki, offered or not."""
        return (*self.offered, *self.left_over, *self.held_back)


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
) -> Offer:
    """The candidates to offer a dreaming run over the wiki at `tree`: what is flagged in it,
    measured against `l0`'s metadata and `thresholds` for `owner`, in the order of `ORDER`,
    less what `history` holds back, and no more than `cap` of them. The rest wait for the next
    run, which this run's outcomes make room for. The `Offer` holds those it leaves out too.

    A candidate that `history` shows an earlier run dismissed is held back until one of its
    pages changes or `DISMISSAL_DAYS` pass, and one an earlier run proposed, while that
    proposal is open in the wiki.

    The ids follow from the wiki and the history alone, so the same wiki on the same day
    offers the same candidates under the same ids.
    """
    report = lint(tree, l0=l0, owner=owner, thresholds=thresholds)
    found = pages(tree)
    linked = _linked_days(tree, thresholds.today)
    flagged = [
        *_stale(found, linked, thresholds),
        *_status_mismatches(found),
        *(_of_warning(warning) for warning in report.warnings),
        *_quiet(found, linked, thresholds),
        *_unengaged(found, report.engaged_days),
    ]
    held = _held_back(history, {proposal.id for proposal in open_proposals(tree)})
    # The sort is stable: within a row of `ORDER`, candidates stay as they were found.
    flagged.sort(key=lambda candidate: _RANK[candidate.kind])
    free = [candidate for candidate in flagged if candidate.identity not in held]
    return Offer(
        offered=tuple(
            replace(candidate, id=f"c{number:02d}")
            for number, candidate in enumerate(free[:cap], start=1)
        ),
        held_back=tuple(candidate for candidate in flagged if candidate.identity in held),
        left_over=tuple(free[cap:]),
    )


def unengaged_without_loops(tree: Path, *, l0: L0Metadata, owner: Owner) -> dict[str, int]:
    """How many pages in the wiki at `tree` `owner` never engaged with, by `l0`, and nothing
    is owed over: a person's or an organization's page that holds no open loop, and that no
    open loop on any page names as a party. By page type (`person`, `organization`).

    They are the pages dreaming may retire, counted whether or not a run would be offered
    them, so the count before a run and after it says what the run cleared.
    """
    found = pages(tree)
    engaged = engaged_days(tree, l0, owner)
    owed = _open_loop_holders(found)
    counts = {PERSON.type: 0, ORGANIZATION.type: 0}
    for candidate in _unengaged(found, engaged):
        [page] = [page for page in found if page.path == candidate.path]
        if page.slug not in owed:
            counts[page.page_type.type] += 1
    return counts


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
    (CandidateKind.UNENGAGED,),
)
"""The fixed order the kinds are offered in, one row per place: what a capped run reaches
first. The module's docstring gives the reasons. A new kind is a row here."""
_RANK = {kind: rank for rank, row in enumerate(ORDER) for kind in row}


def _held_back(history: DreamingHistory, asked: Collection[str]) -> set[Identity]:
    """The candidates not to offer again yet, given the ids of the proposals `asked` of the
    owner and still open. What counts is the newest run that judged a candidate; any older
    outcome for it is superseded.

    A proposed candidate is held back while its proposal is open, so the owner isn't asked the
    same thing twice. Once it is answered, the candidate is offered again if it is still
    flagged.

    A dismissed candidate is held back for `DISMISSAL_DAYS`, or until one of its pages
    changes: until a page's content differs from what the run that dismissed it committed,
    whoever changed it (a librarian run, a later dreaming run, the host rewriting a link in it,
    the owner by hand). A candidate about no page (a slug without one, a marker in a day log)
    waits out the days. A candidate dismissed for lack of room (`no_room`) isn't held back
    at all.
    """
    held: set[Identity] = set()
    judged: set[Identity] = set()
    for run in history.runs:
        for past in run.outcomes:
            identity = past.identity
            if identity in judged:
                continue
            if past.outcome is OutcomeName.PROPOSED:
                waits = past.proposal in asked
            else:
                waits = (
                    past.outcome is OutcomeName.DISMISSED
                    and not past.no_room
                    and run.age < timedelta(days=DISMISSAL_DAYS)
                    and not identity.pages & run.changed
                )
            if waits:
                held.add(identity)
        judged |= {past.identity for past in run.outcomes}
    return held


_UNNUMBERED = ""
"""A candidate's id until `candidates` numbers it."""


def _of_warning(warning: Finding) -> Candidate:
    if warning.kind is None:
        raise ValueError(f"a lint warning without a kind can't be a candidate: {warning}")
    return Candidate(
        id=_UNNUMBERED,
        kind=warning.kind,
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
    holding = _open_loop_holders(pages)
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


def _open_loop_holders(pages: Sequence[Page]) -> dict[str, list[str]]:
    """For each slug with an open loop, the page holding each one, once per loop: a loop is
    its page's, and each of its parties'."""
    holding: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        for loop in page.loops:
            if loop.is_open:
                for slug in {page.slug, loop.owed_by, loop.owed_to}:
                    holding[slug].append(page.path)
    return holding


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


def _unengaged(pages: Sequence[Page], engaged: Collection[str]) -> Iterator[Candidate]:
    """A person's or an organization's page whose slug isn't among the `engaged`: no
    observation linking it cites an episode that is the owner's own doing. A topic is never
    one: it gets its page at the threshold, engaged or not."""
    for page in pages:
        if page.page_type in (PERSON, ORGANIZATION) and page.slug not in engaged:
            yield Candidate(
                id=_UNNUMBERED,
                kind=CandidateKind.UNENGAGED,
                pages=(page.path,),
                path=page.path,
                message=f"an unengaged page: no observation linking [[{page.slug}]] cites an "
                "episode the owner sent, wrote, organized, or attends",
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
