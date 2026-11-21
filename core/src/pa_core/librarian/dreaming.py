"""One dreaming run: the candidates, one session, its checks, and one commit (spec #140).

Dreaming is the librarian's review of the whole wiki. The host flags the candidates
(`pa_core.librarian.candidates`), and one session, the dreaming phase, judges each against L0
and gives it one outcome: applied (L0 settles it, so the session fixed the pages), proposed (it
needs the owner's knowledge, so the session added a proposal to `proposals.md`, or one already
asks), or dismissed. The session reports the outcomes as JSON in its final message.

The run sits beside the librarian run (`pa_core.librarian.run`) and holds the wiki to the same
bar, with the same parts: the session and its resume loop, lint after the session, the host's
deterministic steps (removing merged and retired pages, rewriting links to aliases,
regenerating `index.md`), and one commit, or the wiki back at its last commit if anything
fails. It differs in what it asks and what it records:

- its prompt carries the candidates, the open proposals, and the retired slugs;
- the session may change only pages and `proposals.md`, and there only add proposals: each one
  named by a proposed outcome, numbered above every id the file has held, and within the caps
  on a run's new proposals and on how many are open at once;
- the session may retire a page, by emptying it and naming its slug in an applied outcome: the
  host removes the page and lists the slug in `retired.md`, which only the host writes
  (`pa_core.librarian.retirement`);
- its commit carries the librarian's cursor and version forward from the last commit, so the
  next librarian run and a cut still find them there, and records the candidates and outcomes.

The commit message is the run's whole record (the wiki's git history is dreaming's memory):
a summary line, then each candidate with its outcome under `Candidates:`, then the fields.
The next run reads those lines back (`dreaming_history`) from the dreaming commits that still
stand, so that what was dismissed or proposed isn't judged again every week, and reverting a
dreaming commit also reverts what it remembered.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pa_core.l0 import L0Store
from pa_core.librarian.candidates import (
    CANDIDATE_CAP,
    ORDER,
    Candidate,
    DreamingHistory,
    PastOutcome,
    PastRun,
    candidates,
    unengaged_without_loops,
)
from pa_core.librarian.index import write_index
from pa_core.librarian.links import rewrite_alias_links
from pa_core.librarian.lint import (
    QUIET_DAYS,
    STALE_LATER_DAYS,
    Finding,
    L0Metadata,
    Thresholds,
    lint,
)
from pa_core.librarian.retirement import update_retired
from pa_core.librarian.run import (
    CURSOR_FIELD,
    DREAMING_AT_FIELD,
    LOOP_AGE_DAYS,
    PAGE_THRESHOLD,
    VERSION_FIELD,
    Librarian,
    LibrarianRunError,
    Linter,
    Phase,
    SessionPaths,
    SessionResult,
    SessionRunner,
    WikiRepository,
    _changed,
    _Committed,
    _figure,
    _findings,
    _in_pages,
    _json,
    _remove_merged_pages,
    _session,
    _tree_state,
    commit_fields,
    last_run_fields,
    standing_runs,
)
from pa_core.librarian.wiki import (
    INDEX,
    OPEN,
    OPEN_LOOPS,
    PAGE_FOLDERS,
    PROPOSALS,
    RETIRED,
    Page,
    Proposal,
    citations,
    pages,
    parse_loop,
    read_proposals,
    read_retired,
)
from pa_core.owner import Owner


class _Outcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str
    """The id of the candidate it judges."""


class Applied(_Outcome):
    """L0 settled the candidate, so the session fixed the wiki."""

    outcome: Literal["applied"]
    pages: tuple[str, ...]
    """Every page it changed for this candidate, relative to the wiki; a page merged away or
    retired included."""
    retired: tuple[str, ...] = ()
    """The slugs of the pages it retired (`pa_core.librarian.retirement`): each one a page
    the session emptied, for the host to remove and list in `retired.md`."""


class Proposed(_Outcome):
    """L0 can't settle the candidate and the owner's knowledge would, so the session asks: it
    names a proposal that is open in `proposals.md`.

    With no candidate it is the run's template suggestion: an extra outcome, at most one a
    run, naming a proposal the run added that no candidate's outcome names.
    """

    candidate: str | None = None
    outcome: Literal["proposed"]
    proposal: str
    """The proposal's id, such as `P12`. Several candidates may be gathered into one."""


class Dismissed(_Outcome):
    """The candidate isn't a problem; nothing changed for it."""

    outcome: Literal["dismissed"]
    no_room: bool = False
    """Whether it was set aside only because the run had no proposal left to give it: the
    session would have proposed it. The host doesn't remember such a dismissal, so the next
    run is offered the candidate again (`pa_core.librarian.candidates`)."""


type Outcome = Annotated[Applied | Proposed | Dismissed, Field(discriminator="outcome")]
"""What a dreaming session did about one candidate."""


class Outcomes(BaseModel):
    """What a dreaming session's final message carries: one outcome per candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True)
class DreamingOutcome:
    """What a dreaming run did: nothing (no candidates), or judge them and commit."""

    candidates: tuple[Candidate, ...] = ()
    """The candidates the run offered its session."""
    outcomes: tuple[Outcome, ...] = ()
    """The session's outcome for each candidate, in the candidates' order."""
    suggestion: Proposed | None = None
    """The run's template suggestion, if it made one: the extra outcome with no candidate."""
    commit: str | None = None
    sessions: tuple[tuple[Phase, SessionResult], ...] = ()
    """The run's one session, as a librarian run lists its own."""
    warnings: tuple[Finding, ...] = ()
    """Lint's warnings about the wiki as committed."""
    held_back: int = 0
    """How many candidates the wiki had that the run wasn't offered, because its history
    holds them back (`pa_core.librarian.candidates`). A run with no candidates and some held
    back had nothing to judge, in a wiki that still has what an earlier run flagged."""
    left_over: int = 0
    """How many more it had that the run wasn't offered because of the cap on a run's
    candidates: they wait for the next run."""

    def count(self, outcome: str) -> int:
        """How many candidates got `outcome` (one of `OUTCOMES`)."""
        return sum(o.outcome == outcome for o in self.outcomes)


DREAMING_VERSION_FIELD = "Dreaming-Version"
"""The dreaming commit's field holding the version of the librarian that dreamed, beside the
carried `Librarian-Version`: the librarian whose runs built the wiki."""
CANDIDATES_FIELD = "Dreaming-Candidates"
OUTCOMES = ("applied", "proposed", "dismissed")
"""Every outcome a candidate can get, in the order the commit message counts them (each as
`Dreaming-<Outcome>`)."""
SUGGESTION_FIELD = "Dreaming-Template-Suggestion"
"""The dreaming commit's field holding the id of the proposal that is the run's template
suggestion, when it made one."""
CANDIDATES_HEADING = "Candidates:"
"""What opens the commit message's paragraph listing each candidate with its outcome."""


@dataclass(frozen=True)
class ProposalCaps:
    """How much dreaming may ask of the owner, so that the list stays answerable."""

    new: int = 5
    """The most proposals one run may add."""
    open: int = 15
    """The most proposals that may be open at once: a run adds none past it."""

    def room(self, open_now: int) -> int:
        """How many proposals a run may add when `open_now` are open already."""
        return max(0, min(self.new, self.open - open_now))


PROPOSAL_CAPS = ProposalCaps()
"""The caps on a run's proposals, unless configured otherwise: 5 new, and 15 open."""


def run_dreaming(
    *,
    store: L0Store,
    owner: Owner,
    repository: WikiRepository,
    runner: SessionRunner,
    librarian: Librarian,
    now: datetime,
    page_threshold: int = PAGE_THRESHOLD,
    loop_age_days: int = LOOP_AGE_DAYS,
    stale_later_days: int = STALE_LATER_DAYS,
    quiet_days: int = QUIET_DAYS,
    proposal_caps: ProposalCaps = PROPOSAL_CAPS,
    candidate_cap: int = CANDIDATE_CAP,
) -> DreamingOutcome:
    """Runs dreaming once over the wiki as last committed, and commits what it changed.

    `now` is what the session is told the current time is, and what the candidates are
    measured against, with `page_threshold` and `loop_age_days` as a librarian run's lint has
    them. With no candidates it does nothing. Raises LibrarianRunError when the run fails,
    after restoring the wiki.

    `stale_later_days` and `quiet_days` are as `Thresholds` describes them. `proposal_caps`
    bounds the proposals the run may add, and `candidate_cap` the candidates it is offered.
    """
    tree = repository.tree
    sessions: list[tuple[Phase, SessionResult]] = []
    try:
        # Whatever an interrupted run left behind goes before this one starts.
        repository.discard()
        thresholds = Thresholds(
            now.date(),
            page_threshold,
            loop_age_days,
            stale_later_days=stale_later_days,
            quiet_days=quiet_days,
        )
        l0 = L0Metadata.of(store.envelopes())
        offer = candidates(
            tree,
            l0=l0,
            owner=owner,
            thresholds=thresholds,
            history=dreaming_history(repository, now),
            cap=candidate_cap,
        )
        offered, waiting = offer.offered, len(offer.held_back)
        if not offered:
            return DreamingOutcome(held_back=waiting)
        linter = partial(lint, l0=l0, owner=owner, thresholds=thresholds)
        committed = _Committed(linter(tree).errors)
        result, outcomes, suggestion = _dream(
            offered, tree, runner, now, linter, committed, proposal_caps
        )
        sessions.append((Phase.DREAMING, result))
        rewrite_alias_links(tree)
        write_index(tree)
        report = linter(tree)
        if report.errors:
            raise LibrarianRunError(
                f"the wiki has {len(report.errors)} lint errors"
                f"{committed.blame(report.errors)}:\n" + _findings(report.errors)
            )
        carried = last_run_fields(repository)
        judged = DreamingOutcome(
            candidates=offered,
            outcomes=outcomes,
            suggestion=suggestion,
            sessions=tuple(sessions),
            warnings=report.warnings,
            held_back=waiting,
            left_over=len(offer.left_over),
        )
        commit = repository.commit(dreaming_commit_message(judged, carried, librarian, now))
    except LibrarianRunError as error:
        repository.discard()
        raise LibrarianRunError(
            f"{error}\nThe dreaming run was discarded: the wiki is as last committed.",
            sessions=[*sessions, *error.sessions],
        ) from error
    except BaseException:
        repository.discard()
        raise
    return replace(judged, commit=commit)


@dataclass(frozen=True)
class CandidateCounts:
    """What a dreaming run over a wiki would find, in counts alone: nothing here names a
    page, a slug, or what a candidate is about."""

    flagged: Mapping[str, int]
    """How many candidates are flagged of each kind that has any, in the order the kinds are
    offered in, whether or not a run would be offered them."""
    offered: int
    """How many of them a run now would be offered."""
    held_back: int
    """How many dreaming's memory holds back."""
    left_over: int
    """How many a run now would leave over the cap."""
    cap: int
    unengaged_without_loops: Mapping[str, int]
    """How many pages the owner never engaged with have nothing owed over them, by page type
    (`pa_core.librarian.candidates.unengaged_without_loops`)."""


def count_candidates(
    *,
    store: L0Store,
    owner: Owner,
    repository: WikiRepository,
    now: datetime,
    page_threshold: int = PAGE_THRESHOLD,
    loop_age_days: int = LOOP_AGE_DAYS,
    stale_later_days: int = STALE_LATER_DAYS,
    quiet_days: int = QUIET_DAYS,
    candidate_cap: int = CANDIDATE_CAP,
) -> CandidateCounts:
    """Counts what a dreaming run told that `now` is the time would find in the wiki as it
    is, with the thresholds, cap, and memory `run_dreaming` goes by. It runs no session and
    changes nothing: the counts are what a session that may not read the wiki can report of
    it, before a run and after."""
    thresholds = Thresholds(
        now.date(),
        page_threshold,
        loop_age_days,
        stale_later_days=stale_later_days,
        quiet_days=quiet_days,
    )
    l0 = L0Metadata.of(store.envelopes())
    offer = candidates(
        repository.tree,
        l0=l0,
        owner=owner,
        thresholds=thresholds,
        history=dreaming_history(repository, now),
        cap=candidate_cap,
    )
    flagged = Counter(str(candidate.kind) for candidate in offer.flagged)
    return CandidateCounts(
        flagged={str(kind): flagged[kind] for row in ORDER for kind in row if flagged[kind]},
        offered=len(offer.offered),
        held_back=len(offer.held_back),
        left_over=len(offer.left_over),
        cap=candidate_cap,
        unengaged_without_loops=unengaged_without_loops(repository.tree, l0=l0, owner=owner),
    )


def _dream(
    offered: Sequence[Candidate],
    tree: Path,
    runner: SessionRunner,
    now: datetime,
    linter: Linter,
    committed: _Committed,
    caps: ProposalCaps,
) -> tuple[SessionResult, tuple[Outcome, ...], Proposed | None]:
    """Runs the dreaming session over `offered`, and returns its result, its outcomes, in the
    candidates' order, and its template suggestion, if it made one."""
    before = _tree_state(tree)
    standing = read_proposals(tree)
    retirements = _Retirements(tree, before, now.date())

    def require_bounds() -> None:
        after = _tree_state(tree)
        outside = [
            path
            for path in _changed(before, after)
            if path.partition("/")[0] not in PAGE_FOLDERS and path not in (PROPOSALS, RETIRED)
        ]
        if retirements.changed_by_session(after):
            outside = sorted([*outside, RETIRED])
        if outside:
            raise LibrarianRunError(
                f"the dreaming session changed {', '.join(outside)}, outside the pages; "
                f"dreaming may change only pages, in {', '.join(f'{f}/' for f in PAGE_FOLDERS)}, "
                f"and {PROPOSALS}"
            )

    judged: dict[str | None, Outcome] = {}

    def report_problems(attempt: SessionResult) -> Sequence[str]:
        retirements.undo()
        asked = read_proposals(tree)
        problems = _proposal_problems(standing, asked, caps)
        try:
            reported = _reported(attempt.final_message)
        except InvalidOutcomesError as error:
            return (*error.problems, *problems)
        problems += retirements.carry_out(reported)
        # The bounds held, so everything changed in a folder is a page; a page merged away or
        # retired is gone by now, and gone is changed.
        changed = [path for path in _changed(before, _tree_state(tree)) if "/" in path]
        try:
            outcomes = read_outcomes(
                attempt.final_message,
                offered,
                changed,
                asked=[proposal.id for proposal in asked if proposal.is_open],
                added=[proposal.id for proposal in _added(standing, asked)],
            )
        except InvalidOutcomesError as error:
            return (*error.problems, *problems)
        problems += _room_problems(outcomes, standing, asked, caps)
        applied = [o for o in outcomes if isinstance(o, Applied)]
        claimed = {page for o in applied for page in o.pages}
        # A retired page left out of `pages` is a problem to resume for, above; it is claimed.
        claimed |= {f"{f}/{slug}.md" for o in applied for slug in o.retired for f in PAGE_FOLDERS}
        unclaimed = [path for path in changed if path not in claimed]
        if unclaimed:
            raise LibrarianRunError(
                f"the dreaming session changed {', '.join(unclaimed)}, which no outcome claims; "
                "dreaming may change a page only to settle a candidate, and its applied "
                "outcome names it"
            )
        if problems:
            return problems
        judged.clear()
        judged.update({outcome.candidate: outcome for outcome in outcomes})
        return ()

    # Dreaming may change only pages and the proposals, so it's judged only on the errors in
    # them.
    result = _session(
        Phase.DREAMING,
        dreaming_prompt(offered, runner.paths)
        + "\n"
        + proposals_prompt(standing, caps, runner.paths)
        + "\n"
        + retired_prompt(read_retired(tree), runner.paths),
        require_bounds,
        lambda finding: _in_pages(finding) or finding.path == PROPOSALS,
        tree,
        runner,
        now,
        linter,
        committed,
        tidy=partial(_remove_merged_pages, tree),
        report_problems=report_problems,
        reminder=OUTCOMES_REMINDER,
    )
    suggestion = judged.get(None)
    return (
        result,
        tuple(judged[candidate.id] for candidate in offered),
        suggestion if isinstance(suggestion, Proposed) else None,
    )


class _Retirements:
    """The host's side of the retirements a dreaming session reports
    (`pa_core.librarian.retirement`): removing each retired page, and keeping `retired.md`.

    The session's file tools can't remove a file, so it retires a page as it merges one away,
    by writing it with nothing in it, and an applied outcome names its slug as retired. Only
    an emptied page whose slug an outcome names goes; any other emptied file stays for lint
    to fail, as after a merge.

    The host does this after each of the session's attempts, before lint, so that lint sees
    the wiki as it would be committed: a link to a slug the run retires is good at once. Each
    attempt first undoes what was done for the one before, so what is committed follows from
    the wiki as the session left it and the outcomes of its last message, and from nothing an
    earlier message said.
    """

    def __init__(self, tree: Path, before: Mapping[str, tuple[str, ...]], today: date) -> None:
        """Over the wiki at `tree`, whose state as last committed is `before` (`_tree_state`),
        for a run told the day is `today`."""
        self._tree, self._before, self._today = tree, before, today
        self._was = pages(tree)
        """The pages as last committed."""
        is_file = before.get(RETIRED, ("",))[0] == "file"
        self._listed = (tree / RETIRED).read_bytes() if is_file else None
        """`retired.md` as last committed, when it was a file."""
        self._left = before.get(RETIRED)
        """`retired.md`'s state as the host last left it."""
        self._removed: list[Path] = []
        """The emptied pages the host removed for the last attempt."""

    def changed_by_session(self, after: Mapping[str, tuple[str, ...]]) -> bool:
        """Whether `retired.md` in `after`, the wiki's state as an attempt left it, isn't as the
        host left it: the file is the host's, and no session may write it."""
        return after.get(RETIRED) != self._left

    def undo(self) -> None:
        """Puts the wiki back as the session left it: each page removed for the last attempt
        is an emptied file again, unless the session has written it back, and `retired.md`
        is as last committed."""
        for file in self._removed:
            if not file.exists():
                file.write_bytes(b"")
        self._removed.clear()
        if self._listed is not None:
            (self._tree / RETIRED).write_bytes(self._listed)
        elif RETIRED not in self._before:
            (self._tree / RETIRED).unlink(missing_ok=True)
        self._left = self._before.get(RETIRED)

    def carry_out(self, reported: Sequence[Outcome]) -> list[str]:
        """Removes the pages the `reported` outcomes retire, brings `retired.md` up to date
        with them, and returns what's wrong with the retirements the host can't carry out.

        A retirement can't be carried out when its slug had no page as last committed, when
        the page still has content, and when the page held an open loop that no page holds
        now, open or closed: a loop is never dropped with its page, though it may move to the
        page it belongs on. The page of such a retirement stays as the session left it. A
        retired page its outcome doesn't name among its `pages` goes, and is a problem too.
        """
        problems: list[str] = []
        retiring: list[str] = []
        carried = [set(loop.citations) for page in pages(self._tree) for loop in page.loops]
        for outcome in reported:
            if not isinstance(outcome, Applied):
                continue
            for slug in outcome.retired:
                found = self._retire(slug, carried)
                if isinstance(found, str):
                    problems.append(f"{outcome.candidate} retires `{slug}`, {found}")
                    continue
                retiring.append(slug)
                for path in found:
                    if path not in outcome.pages:
                        problems.append(
                            f"{outcome.candidate} retires `{slug}`, but doesn't name {path} "
                            "among its `pages`: an applied outcome names every page changed "
                            "for it, a retired one included"
                        )
        update_retired(self._tree, today=self._today, retiring=retiring)
        self._left = _tree_state(self._tree).get(RETIRED)
        return problems

    def _retire(self, slug: str, carried: Sequence[set[str]]) -> list[str] | str:
        """Removes the emptied pages of `slug`, and returns their paths; or, when the
        retirement can't be carried out, why not. `carried` is the opening citations of each
        loop the wiki's pages hold now."""
        paths = [path for folder in PAGE_FOLDERS if (path := f"{folder}/{slug}.md") in self._before]
        if not paths:
            return (
                "which had no page when this run started: `retired` lists the slug of each "
                "page you emptied to retire it"
            )
        emptied = [path for path in paths if not _has_content(self._tree / path)]
        if not emptied:
            return (
                f"but {paths[0]} still has content: retire a page by writing it with no "
                "content at all, and the host removes it"
            )
        for page in self._was:
            for line in _open_loop_lines(page) if page.path in emptied else ():
                if not any(set(citations(line)) <= held for held in carried):
                    return (
                        f"but {page.path} held an open loop that is now on no page: `{line}`. "
                        "A page with an open loop is never retired: write the page back as it "
                        "was and propose or dismiss the candidate, or move the loop to the "
                        "page it belongs on"
                    )
        for path in emptied:
            file = self._tree / path
            if file.is_file():
                file.unlink()
                self._removed.append(file)
        return emptied


def _has_content(file: Path) -> bool:
    return file.is_file() and bool(file.read_bytes().strip())


def _open_loop_lines(page: Page) -> list[str]:
    """The lines of `page`'s open loops, as written."""
    section = page.section(OPEN_LOOPS)
    return [
        line
        for _, line in (section.lines if section else ())
        if (loop := parse_loop(line)) is not None and loop.is_open
    ]


def _added(standing: Sequence[Proposal], asked: Sequence[Proposal]) -> list[Proposal]:
    """The proposals in `asked`, the file as a session left it, that weren't in `standing`,
    the file as last committed, by their ids."""
    known = {proposal.id for proposal in standing}
    return [proposal for proposal in asked if proposal.id not in known]


def _proposal_problems(
    standing: Sequence[Proposal], asked: Sequence[Proposal], caps: ProposalCaps
) -> list[str]:
    """What's wrong with how a dreaming session changed `proposals.md`, from `standing` (as
    last committed) to `asked` (as it left it), whatever its outcomes say.

    A dreaming run only adds proposals: every proposal that was there is there still, exactly
    as it was. What it adds is numbered above every id the file held, so an id is never reused,
    and stays within `caps`.
    """
    problems: list[str] = []
    now = {proposal.id: proposal for proposal in reversed(asked)}
    for was in standing:
        if now.get(was.id) != was:
            problems.append(
                f"{was.id} was {'changed' if was.id in now else 'removed'}: a dreaming run only "
                f"adds proposals. Put its line back as it was: `{was.line}`"
            )
    highest = max((proposal.number for proposal in standing), default=0)
    added = _added(standing, asked)
    for proposal in added:
        if proposal.number <= highest:
            problems.append(
                f"{proposal.id} is new, but ids up to P{highest} have been given out: an id is "
                f"never reused, so number new proposals from P{highest + 1}"
            )
    if len(added) > caps.new:
        problems.append(
            f"this run adds {len(added)} proposals, more than the {caps.new} a run may add: "
            f"keep the {caps.new} most useful to the owner, and dismiss the other candidates, "
            'each with `"no_room": true`'
        )
    still_open = sum(proposal.is_open for proposal in asked)
    if added and still_open > caps.open:
        were_open = sum(proposal.is_open for proposal in standing)
        problems.append(
            f"{still_open} proposals would be open, more than the {caps.open} that may be open "
            f"at once: {were_open} were open already, so add at most "
            f"{max(0, caps.open - were_open)}, and dismiss the other candidates, each with "
            '`"no_room": true`'
        )
    return problems


def _room_problems(
    outcomes: Sequence[Outcome],
    standing: Sequence[Proposal],
    asked: Sequence[Proposal],
    caps: ProposalCaps,
) -> list[str]:
    """What's wrong with the candidates a session dismissed for lack of room (`no_room`),
    given the proposals as last committed (`standing`) and as it left them (`asked`): there
    is none while the run may still add a proposal within `caps`. Such a dismissal isn't
    remembered, so it must be what it says."""
    left = caps.room(sum(proposal.is_open for proposal in standing)) - len(_added(standing, asked))
    if left <= 0:
        return []
    return [
        f"{outcome.candidate} is dismissed for lack of room, but this run may still add {left} "
        "proposals: propose it, or dismiss it without `no_room` if it isn't worth asking"
        for outcome in outcomes
        if isinstance(outcome, Dismissed) and outcome.no_room
    ]


OUTCOMES_REMINDER = (
    "\nThen end your reply with the outcomes again, complete and as CLAUDE.md describes them: "
    "one for every candidate, each applied one naming every page you have changed for it in "
    "this session. The host reads only the outcomes in your last message.\n"
)
"""What ends every prompt that resumes a dreaming session, whatever it was resumed for."""


class InvalidOutcomesError(Exception):
    """A dreaming session's final message without outcomes the host can accept."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)
        """Each thing wrong with them, as the session is told when it's resumed."""


_FENCED_JSON = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
_NO_OUTCOMES = (
    "your final message holds no outcomes: end it with a fenced `json` block holding "
    '`{"outcomes": [...]}`, one outcome for each candidate'
)


def _reported(message: str | None) -> tuple[Outcome, ...]:
    """The outcomes a dreaming session's final `message` carries, in its last fenced `json`
    block (or as the whole message), as reported: in the outcomes' shape, and checked against
    nothing else. Raises InvalidOutcomesError when it carries none, or they aren't in that
    shape."""
    text = (message or "").strip()
    blocks = _FENCED_JSON.findall(text)
    if not blocks and not text.startswith("{"):
        raise InvalidOutcomesError([_NO_OUTCOMES])
    try:
        return Outcomes.model_validate(json.loads(blocks[-1] if blocks else text)).outcomes
    except json.JSONDecodeError as error:
        raise InvalidOutcomesError(
            [f"your final message's outcomes aren't JSON: {error}"]
        ) from error
    except ValidationError as error:
        raise InvalidOutcomesError(
            [
                f"{'.'.join(str(part) for part in found['loc']) or 'the outcomes'}: {found['msg']}"
                for found in error.errors()
            ]
        ) from error


def read_outcomes(
    message: str | None,
    offered: Sequence[Candidate],
    changed: Sequence[str],
    *,
    asked: Collection[str] = (),
    added: Collection[str] = (),
) -> tuple[Outcome, ...]:
    """The outcomes a dreaming session's final `message` carries, in its last fenced `json`
    block (or as the whole message), checked against the candidates it was `offered`, the
    paths it `changed`, relative to the wiki, and the proposals in `proposals.md` as the
    session left it: the ids of those `asked` of the owner (the open ones), and of those the
    session `added`.

    Raises InvalidOutcomesError unless they are in the outcomes' shape; every candidate has
    exactly one, and none is for anything else but one template suggestion (a proposed outcome
    with no candidate); each applied outcome names pages, all of which changed; each proposed
    outcome names an open proposal, the suggestion one of its own that the session added; and
    every proposal the session added is named.
    """
    outcomes = _reported(message)
    problems: list[str] = []
    given = Counter(outcome.candidate for outcome in outcomes if outcome.candidate is not None)
    for candidate in offered:
        if given[candidate.id] == 0:
            problems.append(f"{candidate.id} has no outcome: every candidate gets exactly one")
        elif given[candidate.id] > 1:
            problems.append(
                f"{candidate.id} has {given[candidate.id]} outcomes: every candidate gets "
                "exactly one"
            )
    for name in sorted(given.keys() - {candidate.id for candidate in offered}):
        problems.append(f"{name} isn't one of this run's candidates")
    suggestions = [outcome for outcome in outcomes if outcome.candidate is None]
    if len(suggestions) > 1:
        problems.append(
            f"{len(suggestions)} outcomes have no candidate: a run makes at most one template "
            "suggestion, and every other outcome names its candidate"
        )
    named = {o.proposal for o in outcomes if isinstance(o, Proposed)}
    for name in added:
        if name not in named:
            problems.append(
                f"{name} is new in {PROPOSALS}, but no outcome names it: a proposal is added "
                "only for a proposed outcome, which names it"
            )
    for_candidates = {
        o.proposal for o in outcomes if isinstance(o, Proposed) and o.candidate is not None
    }
    for outcome in outcomes:
        if isinstance(outcome, Proposed) and outcome.candidate is None:
            if outcome.proposal not in added:
                problems.append(
                    f"the template suggestion names {outcome.proposal}, which isn't a proposal "
                    f"this run added to {PROPOSALS}"
                )
            elif outcome.proposal in for_candidates:
                problems.append(
                    f"the template suggestion names {outcome.proposal}, which a candidate's "
                    "outcome names too: a suggestion is a proposal of its own"
                )
        elif isinstance(outcome, Proposed) and outcome.proposal not in asked:
            problems.append(
                f"{outcome.candidate} names {outcome.proposal}, which isn't an open proposal in "
                f"{PROPOSALS}: a proposed outcome names the proposal you added for it under "
                f"`## {OPEN}`, by its id"
            )
        if not isinstance(outcome, Applied):
            continue
        if not outcome.pages:
            problems.append(
                f"{outcome.candidate} is applied but names no page: an applied outcome names "
                "every page changed for it, and a candidate nothing changed for is dismissed"
            )
        for page in outcome.pages:
            if page not in changed:
                problems.append(
                    f"{outcome.candidate} names {page}, which this session didn't change: name "
                    "only pages you changed, by their paths relative to the wiki"
                )
    if problems:
        raise InvalidOutcomesError(problems)
    return outcomes


CANDIDATES_PROMPT_HEADING = "## Candidates"


def dreaming_prompt(offered: Sequence[Candidate], paths: SessionPaths) -> str:
    """What the dreaming session is asked: the candidates, as a JSON block under
    `CANDIDATES_PROMPT_HEADING`."""
    folders = [f"`{paths.wiki / folder}`" for folder in PAGE_FOLDERS]
    return (
        f"Phase: {Phase.DREAMING}\n\n"
        "Review the wiki, following CLAUDE.md's Dreaming section. The host has flagged the "
        f"{len(offered)} candidate problems below: judge each one against L0, and give each "
        "exactly one outcome. Read any page, any day log, and any episode you need, in "
        f"`{paths.wiki}` and `{paths.l0}`. Write only pages, in {', '.join(folders[:-1])}, and "
        f"{folders[-1]}, and only to settle a candidate; and `{paths.wiki / PROPOSALS}`, only "
        "to add a proposal, as the Open proposals section below says. Never change a day log, "
        f"and never write `{INDEX}`: the host generates it.\n\n"
        "End your final message with the outcomes, as CLAUDE.md describes them: the run can't "
        "be committed without one outcome for every candidate.\n\n"
        f"{CANDIDATES_PROMPT_HEADING}\n\n"
        "Each candidate has its `id`, its `kind`, the `pages` it is about, and the file "
        "(`path`, and `line` when it is on one) and `message` of what the host flagged, with "
        "paths relative to the wiki. A loop's candidate lists the `episodes` its line cites, "
        "since its line number moves as the page is edited; a slug without a page gives the "
        "`slug`.\n\n"
        f"```json\n{_json([candidate.to_json() for candidate in offered])}\n```\n"
    )


PROPOSALS_PROMPT_HEADING = "## Open proposals"
NO_ROOM_SAID = 'with `"no_room": true` in its outcome, so that the next run is offered it again'
"""How a prompt says to dismiss a candidate the run has no proposal left for."""


def proposals_prompt(standing: Sequence[Proposal], caps: ProposalCaps, paths: SessionPaths) -> str:
    """The proposals as a section of the dreaming session's prompt, under
    `PROPOSALS_PROMPT_HEADING`: the open ones among `standing` (the file as last committed) as
    a JSON block, how many the run may add within `caps`, and the id the first one takes."""
    asked = [proposal for proposal in standing if proposal.is_open]
    room = caps.room(len(asked))
    first = max((proposal.number for proposal in standing), default=0) + 1
    if room:
        may = (
            f"You may add at most {room} in this run ({len(asked)} are open, and at most "
            f"{caps.open} may be open at once): number them from P{first}, in the order you "
            "add them, since an id is never reused. Dismiss a candidate you would have proposed "
            f"past that {NO_ROOM_SAID}."
        )
    else:
        may = (
            f"You may add none in this run: {len(asked)} are open already, and at most "
            f"{caps.open} may be open at once. Dismiss a candidate you would have proposed "
            f"{NO_ROOM_SAID}."
        )
    return (
        f"{PROPOSALS_PROMPT_HEADING}\n\n"
        "What the librarian has asked the owner, and the owner hasn't answered yet: the open "
        f"proposals in `{paths.wiki / PROPOSALS}`, each listed below with the `episodes` it "
        "cites. A candidate that one of them already asks about gets a proposed outcome naming "
        "it; never ask the same thing twice. For any other candidate CLAUDE.md says to "
        f"propose, add a line under `## {OPEN}`, creating the file as `wiki-schema.md` "
        f"describes it if the wiki has none. {may} Never change or remove a proposal that is "
        "already in the file.\n\n"
        f"```json\n{_json([proposal.to_json() for proposal in asked])}\n```\n"
    )


RETIRED_PROMPT_HEADING = "## Retired slugs"


def retired_prompt(retired: Mapping[str, date], paths: SessionPaths) -> str:
    """The retired slugs as a section of the dreaming session's prompt, under
    `RETIRED_PROMPT_HEADING`: each of `retired` with the day it was retired, as a JSON block."""
    listed = [{"slug": slug, "retired": day.isoformat()} for slug, day in retired.items()]
    return (
        f"{RETIRED_PROMPT_HEADING}\n\n"
        "The slugs whose pages earlier dreaming runs retired, each with the day it was "
        f"retired: what `{paths.wiki / RETIRED}` lists. A link to one is good, and it has no "
        "page on purpose: don't create one unless a candidate is about its slug. To retire a "
        "page in this run, follow CLAUDE.md: empty the page, and name its slug in your "
        f"outcome. Never write `{RETIRED}` yourself: the host keeps it.\n\n"
        f"```json\n{_json(listed)}\n```\n"
    )


def dreaming_commit_message(
    outcome: DreamingOutcome, carried: Mapping[str, str], librarian: Librarian, now: datetime
) -> str:
    """A dreaming run's commit message: a summary line, each candidate with its outcome as a
    list under `CANDIDATES_HEADING`, then one `Field: value` line per fact, in the last
    paragraph, where `commit_fields` reads them.

    `carried` is the fields of the last librarian run that stands (`last_run_fields`): its
    cursor and librarian version are carried forward as they are, when it has them.
    """
    counts = [(name, outcome.count(name)) for name in OUTCOMES]
    fields: list[tuple[str, object]] = [
        *((key, carried[key]) for key in (CURSOR_FIELD, VERSION_FIELD) if key in carried),
        (DREAMING_VERSION_FIELD, librarian.version),
        ("Model", librarian.model),
        ("Effort", librarian.effort),
        (DREAMING_AT_FIELD, now.astimezone(UTC).isoformat()),
    ]
    for phase, result in outcome.sessions:
        name = phase.value.capitalize()
        fields += [
            (f"{name}-Cost-USD", _figure(result.cost_usd)),
            (f"{name}-Turns", _figure(result.turns)),
            (f"{name}-Session", result.session_id or "unknown"),
            (f"{name}-Resumes", result.resumes),
        ]
    fields.append((CANDIDATES_FIELD, len(outcome.candidates)))
    fields += [(f"Dreaming-{name.capitalize()}", count) for name, count in counts]
    if outcome.suggestion is not None:
        fields.append((SUGGESTION_FIELD, outcome.suggestion.proposal))
    fields.append(("Lint-Warnings", len(outcome.warnings)))
    summary = f"dreaming run: {len(outcome.candidates)} candidates, " + ", ".join(
        f"{count} {name}" for name, count in counts
    )
    listed = [
        _listed(candidate, judged)
        for candidate, judged in zip(outcome.candidates, outcome.outcomes, strict=True)
    ]
    paragraphs = [
        summary,
        CANDIDATES_HEADING + "\n" + "\n".join(listed),
        "\n".join(f"{key}: {value}" for key, value in fields),
    ]
    return "\n\n".join(paragraphs) + "\n"


def _listed(candidate: Candidate, outcome: Outcome) -> str:
    """A candidate's line in the commit message: its id, kind, and subject, then its outcome,
    an applied one with the pages it changed, a proposed one with its proposal, and one
    dismissed for lack of room saying so."""
    did = outcome.outcome
    if isinstance(outcome, Applied):
        did += ": " + ", ".join(outcome.pages)
    elif isinstance(outcome, Proposed):
        did += ": " + outcome.proposal
    elif outcome.no_room:
        did += ": " + NO_ROOM
    return f"- {candidate.id} · {candidate.kind} · {candidate.subject} · {did}"


_LISTED = re.compile(
    r"^- c\d+ · (?P<kind>[^ ·]+) · (?P<subject>.+) · (?P<outcome>"
    + "|".join(OUTCOMES)
    + r")(?:: (?P<detail>.+))?$"
)
"""A candidate's line, as `_listed` writes it."""
NO_ROOM = "no room"
"""What follows `dismissed` in the line of a candidate dismissed only for lack of room."""


def last_dreaming_at(repository: WikiRepository) -> datetime | None:
    """The time the last dreaming run that still stands was told was now (its `Dreaming-At`),
    or None for a wiki no standing run has dreamed. A reverted run doesn't count, and neither
    does a run that found no candidates, which commits nothing."""
    for commit in standing_runs(repository):
        at = commit_fields(commit.message).get(DREAMING_AT_FIELD)
        if at is not None:
            return datetime.fromisoformat(at)
    return None


def dreaming_history(repository: WikiRepository, now: datetime) -> DreamingHistory:
    """What dreaming remembers of the wiki in `repository`, for a run told that `now` is the
    time: each earlier dreaming run, newest first, with how long ago it ran, what it decided
    about each of its candidates, and what has changed since, read back from the dreaming
    commits' messages. A run's age is measured from the time it was told, never from its
    commit's date.

    Only runs that still stand are remembered (`standing_runs`): reverting a dreaming commit
    forgets what it decided, and a wiki with no history has none.
    """
    runs: list[PastRun] = []
    for commit in standing_runs(repository):
        at = commit_fields(commit.message).get(DREAMING_AT_FIELD)
        if at is None:
            continue
        listed = (_LISTED.match(line) for line in commit.message.splitlines())
        runs.append(
            PastRun(
                age=now - datetime.fromisoformat(at),
                outcomes=tuple(
                    PastOutcome(
                        kind=found["kind"],
                        subject=found["subject"],
                        outcome=found["outcome"],
                        proposal=found["detail"] if found["outcome"] == "proposed" else None,
                        no_room=found["outcome"] == "dismissed" and found["detail"] == NO_ROOM,
                    )
                    for found in listed
                    if found
                ),
                changed=frozenset(repository.changed_since(commit.id)),
            )
        )
    return DreamingHistory(tuple(runs))
