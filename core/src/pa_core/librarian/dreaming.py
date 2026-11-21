"""One dreaming run: the candidates, one session, its checks, and one commit (spec #140).

Dreaming is the librarian's review of the whole wiki. The host flags the candidates
(`pa_core.librarian.candidates`), and one session, the dreaming phase, judges each against L0
and gives it one outcome: applied (L0 settles it, so the session fixed the pages) or dismissed.
The session reports the outcomes as JSON in its final message.

The run sits beside the nightly run (`pa_core.librarian.run`) and holds the wiki to the same
bar, with the same parts: the session and its resume loop, lint after the session, the host's
deterministic steps (removing merged pages, rewriting links to aliases, regenerating
`index.md`), and one commit, or the wiki back at its last commit if anything fails. It differs
in what it asks and what it records:

- its prompt carries the candidates;
- the session may change only pages;
- its commit carries the librarian's cursor and version forward from the last commit, so the
  next nightly run and a cut still find them there, and records the candidates and outcomes.

The commit message is the run's whole record (the wiki's git history is dreaming's memory):
a summary line, then each candidate with its outcome under `Candidates:`, then the fields.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pa_core.l0 import L0Store
from pa_core.librarian.candidates import Candidate, candidates
from pa_core.librarian.index import write_index
from pa_core.librarian.links import rewrite_alias_links
from pa_core.librarian.lint import STALE_LATER_DAYS, Finding, L0Metadata, Thresholds, lint
from pa_core.librarian.run import (
    CURSOR_FIELD,
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
)
from pa_core.librarian.wiki import INDEX, PAGE_FOLDERS
from pa_core.owner import Owner


class _Outcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: str
    """The id of the candidate it judges."""


class Applied(_Outcome):
    """L0 settled the candidate, so the session fixed the wiki."""

    outcome: Literal["applied"]
    pages: tuple[str, ...]
    """Every page it changed for this candidate, relative to the wiki; a page merged away
    included."""


class Dismissed(_Outcome):
    """The candidate isn't a problem, or L0 can't settle it; nothing changed for it."""

    outcome: Literal["dismissed"]


type Outcome = Annotated[Applied | Dismissed, Field(discriminator="outcome")]
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
    commit: str | None = None
    sessions: tuple[tuple[Phase, SessionResult], ...] = ()
    """The run's one session, as a nightly run lists its own."""
    warnings: tuple[Finding, ...] = ()
    """Lint's warnings about the wiki as committed."""

    def count(self, outcome: str) -> int:
        """How many candidates got `outcome` (`applied`, `dismissed`)."""
        return sum(o.outcome == outcome for o in self.outcomes)


DREAMING_AT_FIELD = "Dreaming-At"
"""The dreaming commit's field holding the time the run was told is now: what marks a commit as
a dreaming run's, and what intervals between dreaming runs are measured from (the commit's own
date is the clock's, whatever the run was told)."""
DREAMING_VERSION_FIELD = "Dreaming-Version"
"""The dreaming commit's field holding the version of the librarian that dreamed, beside the
carried `Librarian-Version`: the librarian whose runs built the wiki."""
CANDIDATES_FIELD = "Dreaming-Candidates"
OUTCOMES = ("applied", "dismissed")
"""Every outcome a candidate can get, in the order the commit message counts them (each as
`Dreaming-<Outcome>`)."""
CANDIDATES_HEADING = "Candidates:"
"""What opens the commit message's paragraph listing each candidate with its outcome."""


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
) -> DreamingOutcome:
    """Runs dreaming once over the wiki as last committed, and commits what it changed.

    `now` is what the session is told the current time is, and what the candidates are
    measured against, with `page_threshold` and `loop_age_days` as a nightly run's lint has
    them. With no candidates it does nothing. Raises LibrarianRunError when the run fails,
    after restoring the wiki.

    `stale_later_days` is as `Thresholds` describes it.
    """
    tree = repository.tree
    sessions: list[tuple[Phase, SessionResult]] = []
    try:
        # Whatever an interrupted run left behind goes before this one starts.
        repository.discard()
        thresholds = Thresholds(
            now.date(), page_threshold, loop_age_days, stale_later_days=stale_later_days
        )
        l0 = L0Metadata.of(store.envelopes())
        offered = candidates(tree, l0=l0, owner=owner, thresholds=thresholds)
        if not offered:
            return DreamingOutcome()
        linter = partial(lint, l0=l0, owner=owner, thresholds=thresholds)
        committed = _Committed(linter(tree).errors)
        result, outcomes = _dream(offered, tree, runner, now, linter, committed)
        sessions.append((Phase.DREAMING, result))
        rewrite_alias_links(tree)
        write_index(tree)
        report = linter(tree)
        if report.errors:
            raise LibrarianRunError(
                f"the wiki has {len(report.errors)} lint errors"
                f"{committed.blame(report.errors)}:\n" + _findings(report.errors)
            )
        carried = commit_fields(repository.last_commit_message() or "")
        judged = DreamingOutcome(
            candidates=offered,
            outcomes=outcomes,
            sessions=tuple(sessions),
            warnings=report.warnings,
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


def _dream(
    offered: Sequence[Candidate],
    tree: Path,
    runner: SessionRunner,
    now: datetime,
    linter: Linter,
    committed: _Committed,
) -> tuple[SessionResult, tuple[Outcome, ...]]:
    """Runs the dreaming session over `offered`, and returns its result and its outcomes, in
    the candidates' order."""
    before = _tree_state(tree)

    def require_bounds() -> None:
        outside = [
            path
            for path in _changed(before, _tree_state(tree))
            if path.partition("/")[0] not in PAGE_FOLDERS
        ]
        if outside:
            raise LibrarianRunError(
                f"the dreaming session changed {', '.join(outside)}, outside the pages; "
                f"dreaming may change only pages, in {', '.join(f'{f}/' for f in PAGE_FOLDERS)}"
            )

    judged: dict[str, Outcome] = {}

    def report_problems(attempt: SessionResult) -> Sequence[str]:
        # The bounds held, so everything changed is in a page folder; a page merged away is
        # gone by now, and gone is changed.
        changed = [path for path in _changed(before, _tree_state(tree)) if "/" in path]
        try:
            outcomes = read_outcomes(attempt.final_message, offered, changed)
        except InvalidOutcomesError as error:
            return error.problems
        claimed = {page for o in outcomes if isinstance(o, Applied) for page in o.pages}
        unclaimed = [path for path in changed if path not in claimed]
        if unclaimed:
            raise LibrarianRunError(
                f"the dreaming session changed {', '.join(unclaimed)}, which no outcome claims; "
                "dreaming may change a page only to settle a candidate, and its applied "
                "outcome names it"
            )
        judged.update({outcome.candidate: outcome for outcome in outcomes})
        return ()

    # Dreaming may change only pages, so it's judged only on the errors in them.
    result = _session(
        Phase.DREAMING,
        dreaming_prompt(offered, runner.paths),
        require_bounds,
        _in_pages,
        tree,
        runner,
        now,
        linter,
        committed,
        tidy=partial(_remove_merged_pages, tree),
        report_problems=report_problems,
        reminder=OUTCOMES_REMINDER,
    )
    return result, tuple(judged[candidate.id] for candidate in offered)


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


def read_outcomes(
    message: str | None, offered: Sequence[Candidate], changed: Sequence[str]
) -> tuple[Outcome, ...]:
    """The outcomes a dreaming session's final `message` carries, in its last fenced `json`
    block (or as the whole message), checked against the candidates it was `offered` and the
    paths it `changed`, relative to the wiki.

    Raises InvalidOutcomesError unless they are in the outcomes' shape, every candidate has
    exactly one, none is for anything else, and each applied outcome names pages, all of which
    changed.
    """
    text = (message or "").strip()
    blocks = _FENCED_JSON.findall(text)
    if not blocks and not text.startswith("{"):
        raise InvalidOutcomesError([_NO_OUTCOMES])
    try:
        outcomes = Outcomes.model_validate(json.loads(blocks[-1] if blocks else text)).outcomes
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
    problems: list[str] = []
    given = Counter(outcome.candidate for outcome in outcomes)
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
    for outcome in outcomes:
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
        f"{folders[-1]}, and only to settle a candidate. Never change a day log, and never "
        f"write `{INDEX}`: the host generates it.\n\n"
        "End your final message with the outcomes, as CLAUDE.md describes them: the run can't "
        "be committed without one outcome for every candidate.\n\n"
        f"{CANDIDATES_PROMPT_HEADING}\n\n"
        "Each candidate has its `id`, its `kind`, the `pages` it is about, and the file "
        "(`path`, and `line` when it is on one) and `message` of what the host found, with "
        "paths relative to the wiki. A loop's candidate lists the `episodes` its line cites, "
        "since its line number moves as the page is edited; a slug without a page gives the "
        "`slug`.\n\n"
        f"```json\n{_json([candidate.to_json() for candidate in offered])}\n```\n"
    )


def dreaming_commit_message(
    outcome: DreamingOutcome, carried: Mapping[str, str], librarian: Librarian, now: datetime
) -> str:
    """A dreaming run's commit message: a summary line, each candidate with its outcome as a
    list under `CANDIDATES_HEADING`, then one `Field: value` line per fact, in the last
    paragraph, where `commit_fields` reads them.

    `carried` is the last commit's fields: its cursor and librarian version are carried
    forward as they are, when it has them.
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
    an applied one with the pages it changed."""
    did = outcome.outcome
    if isinstance(outcome, Applied):
        did += ": " + ", ".join(outcome.pages)
    return f"- {candidate.id} · {candidate.kind} · {candidate.subject} · {did}"
