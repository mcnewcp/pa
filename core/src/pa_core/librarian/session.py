"""What a librarian run and a dreaming run share (`pa_core.librarian.run`,
`pa_core.librarian.dreaming`): a session, and what the host does around one.

- **The session port**: `SessionRunner`, which an adapter implements to run the librarian's
  sessions wherever they're fenced, with a session's `Phase`, `SessionPaths`, and
  `SessionResult`.
- **The session loop**: `run_session` runs a phase's session, tidies the wiki, checks the
  session's bounds, lints, and resumes the session with what it left, at most `MAX_RESUMES`
  times. A run that can't go on raises `LibrarianRunError`.
- **Tree states**: `tree_state` and `changed_paths`, which a phase's bounds are checked with.
- **The host's tidying**: `remove_merged_pages`.
- **The commit message's fields**: the `Field: value` paragraph that ends a run's commit
  message, which `commit_fields` reads back, and the figures written into it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol

from pa_core.errors import PaError
from pa_core.librarian.lint import Finding, LintReport
from pa_core.librarian.wiki import PAGE_FOLDERS, folder_files, is_page_name, pages, slugify


class Phase(StrEnum):
    """A run's sessions, in the order they run."""

    OBSERVATION = "observation"
    """Writes one observation per relevant episode into the day log for its day."""
    CONSOLIDATION = "consolidation"
    """Folds the run's new observations into pages, creating those that have come up enough."""
    DREAMING = "dreaming"
    """A dreaming run's one session (`pa_core.librarian.dreaming`): judges the candidates the
    host flagged over the whole wiki."""


@dataclass(frozen=True)
class SessionPaths:
    """Where a session sees L0 and the wiki, which may differ from the host's paths."""

    l0: PurePosixPath
    wiki: PurePosixPath


@dataclass(frozen=True)
class SessionResult:
    """How a session ended, and what it cost, as the session reports it."""

    ok: bool
    """Whether it finished without an error or a timeout."""
    session_id: str | None = None
    """The id to resume it by, if it reported one."""
    cost_usd: float | None = None
    turns: int | None = None
    error: str | None = None
    """What went wrong, when it didn't finish."""
    resumes: int = 0
    """How many times the run resumed it to fix what it left."""
    final_message: str | None = None
    """The last thing it said, which is where a dreaming session reports its outcomes. Over a
    session's attempts, its last attempt's."""


class SessionRunner(Protocol):
    """Runs the librarian's sessions in the librarian project, with only the file tools."""

    @property
    def paths(self) -> SessionPaths:
        """Where the sessions see L0 and the wiki."""
        ...

    def start(self, phase: Phase, prompt: str, *, now: datetime) -> SessionResult:
        """Runs a new session for `phase` on `prompt`, telling it `now` is the current time."""
        ...

    def resume(self, session_id: str, prompt: str, *, now: datetime) -> SessionResult:
        """Continues session `session_id` with the follow-up `prompt`."""
        ...


class LibrarianRunError(PaError):
    """A run that failed; the wiki was left as last committed."""

    def __init__(
        self, message: str, *, sessions: Sequence[tuple[Phase, SessionResult]] = ()
    ) -> None:
        super().__init__(message)
        self.sessions = tuple(sessions)
        """The sessions the run ran before it failed, in order, the one that failed included
        (with what it reported up to then)."""


type Linter = Callable[[Path], LintReport]
"""Lints the wiki at a path, for this run."""


@dataclass(frozen=True)
class Committed:
    """The lint errors the wiki had as last committed, before this run changed anything."""

    errors: Sequence[Finding]

    def blame(self, errors: Sequence[Finding]) -> str:
        """What a failure over `errors` adds: that the last commit already had them all, when
        it did."""
        already = {finding_key(e) for e in self.errors}
        if errors and all(finding_key(e) in already for e in errors):
            return (
                ", all of them already in the wiki's last commit (this run didn't cause them; "
                "a rule may have been tightened since, or the wiki was seeded with them)"
            )
        return ""


def finding_key(finding: Finding) -> tuple[str, str]:
    """What makes a finding the same one from lint to lint: its file and what it says. Not
    its line: a finding is the same one when lines above it were added."""
    return finding.path, finding.message


def in_pages(finding: Finding) -> bool:
    """Whether `finding` is about a page."""
    return finding.path.partition("/")[0] in PAGE_FOLDERS


def findings_text(findings: Sequence[Finding]) -> str:
    """`findings` as a run's error lists them, one to a line."""
    return "\n".join(f"  {finding}" for finding in findings)


MAX_RESUMES = 2
"""How many times a session that leaves lint errors is resumed to fix them."""


def run_session(
    phase: Phase,
    prompt: str,
    require_bounds: Callable[[], None],
    judged: Callable[[Finding], bool],
    tree: Path,
    runner: SessionRunner,
    now: datetime,
    linter: Linter,
    committed: Committed,
    *,
    tidy: Callable[[], None] = lambda: None,
) -> SessionResult:
    """Runs `phase`'s session on `prompt`, then `tidy`s the wiki, checks its bounds, and lints
    it. A session that leaves lint errors it can fix (those `judged` picks) is resumed with
    them, up to MAX_RESUMES times; the rest are left to the next phase, or to the run's last
    lint. Returns the session's result, its cost and turns added up over its attempts.
    """
    result, _ = run_reporting_session(
        phase,
        prompt,
        require_bounds,
        judged,
        tree,
        runner,
        now,
        linter,
        committed,
        tidy=tidy,
        act_on_report=lambda _: None,
    )
    return result


@dataclass(frozen=True)
class Refused:
    """What the host can't accept of what a session's final message reported."""

    problems: Sequence[str]
    """Each thing wrong with it, as the session is told when it's resumed."""


def run_reporting_session[Accepted](
    phase: Phase,
    prompt: str,
    require_bounds: Callable[[], None],
    judged: Callable[[Finding], bool],
    tree: Path,
    runner: SessionRunner,
    now: datetime,
    linter: Linter,
    committed: Committed,
    *,
    act_on_report: Callable[[SessionResult], Accepted | Refused],
    tidy: Callable[[], None] = lambda: None,
    reminder: str = "",
) -> tuple[SessionResult, Accepted]:
    """Runs the session of a `phase` that must report something in its final message
    (dreaming's outcomes), as `run_session` runs any, and returns its result with what the
    host accepted of its last report.

    After each attempt's bounds hold, `act_on_report` reads the attempt's report and does the
    host's part of what it says, which may change the wiki (dreaming removes the pages an
    outcome retires), before lint sees it. It returns what it accepted, or `Refused` with
    what's wrong, or raises when the attempt can't be put right. What is refused resumes the
    session, as lint errors do and within the same limit. Every resume's prompt ends with
    `reminder`.
    """
    attempts = [runner.start(phase, prompt, now=now)]
    try:
        while True:
            result = attempts[-1]
            if not result.ok:
                raise LibrarianRunError(f"the {phase} session failed: {result.error}")
            tidy()
            require_bounds()
            accepted = act_on_report(result)
            problems = accepted.problems if isinstance(accepted, Refused) else ()
            errors = [e for e in linter(tree).errors if judged(e)]
            if not isinstance(accepted, Refused) and not errors:
                return _combined(attempts), accepted
            if len(attempts) > MAX_RESUMES or attempts[0].session_id is None:
                resumed = f" after {len(attempts) - 1} resumes" if len(attempts) > 1 else ""
                left = []
                if problems:
                    left.append(
                        f"the {phase} session's final message can't be accepted{resumed}:\n"
                        + "\n".join(f"  {problem}" for problem in problems)
                    )
                if errors:
                    left.append(
                        f"the {phase} session left {len(errors)} lint errors{resumed}"
                        f"{committed.blame(errors)}:\n" + findings_text(errors)
                    )
                raise LibrarianRunError("\n".join(left))
            attempts.append(
                runner.resume(
                    attempts[0].session_id,
                    fix_prompt(phase, errors, problems) + reminder,
                    now=now,
                )
            )
    except LibrarianRunError as error:
        # The run log records a failed session too, with what it reported.
        raise LibrarianRunError(str(error), sessions=[(phase, _combined(attempts))]) from error


def _combined(attempts: Sequence[SessionResult]) -> SessionResult:
    """One session's result over its attempts: what it cost and the turns it took, in all."""
    costs = [a.cost_usd for a in attempts if a.cost_usd is not None]
    turns = [a.turns for a in attempts if a.turns is not None]
    return replace(
        attempts[0],
        cost_usd=sum(costs) if costs else None,
        turns=sum(turns) if turns else None,
        resumes=len(attempts) - 1,
        final_message=attempts[-1].final_message,
    )


def fix_prompt(phase: Phase, errors: Sequence[Finding], problems: Sequence[str] = ()) -> str:
    """What a session is told when it's resumed: the `problems` with what its final message
    reported, if any, then the lint `errors` it left, if any."""
    parts = [f"Phase: {phase}\n"]
    if problems:
        listed = "\n".join(f"- {problem}" for problem in problems)
        parts.append(
            "The host can't accept what your final message reported, and the run can't be "
            "committed until it can. Put each of these right, following CLAUDE.md and staying "
            f"within this phase's bounds:\n\n{listed}\n"
        )
    if errors:
        findings = "\n".join(f"- {finding}" for finding in errors)
        parts.append(
            f"Lint found {len(errors)} errors in the wiki after your work, and the run can't be "
            "committed until they are fixed. Fix each one, following CLAUDE.md and staying "
            "within this phase's bounds; change nothing else. Each finding is `<path>[:<line>]: "
            "<problem>`, with paths relative to the wiki.\n\n"
            f"{findings}\n"
        )
    return "\n".join(parts)


type TreeState = dict[str, tuple[str, ...]]


def tree_state(tree: Path) -> TreeState:
    """Every file, link, and directory under `tree`, without following links."""
    state: TreeState = {}
    for parent, directories, files in os.walk(tree):
        for name in directories + files:
            path = Path(parent) / name
            key = path.relative_to(tree).as_posix()
            if path.is_symlink():
                state[key] = ("link", str(path.readlink()))
            elif path.is_dir():
                state[key] = ("directory",)
            elif path.is_file():
                state[key] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                state[key] = ("other",)
    return state


def changed_paths(before: TreeState, after: TreeState) -> list[str]:
    """The paths added, removed, or changed between two states of a tree."""
    return sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )


def remove_merged_pages(tree: Path) -> None:
    """Removes the pages a consolidation session merged away. Its file tools can't remove a
    file, so it deletes a page merged into another by writing it with nothing in it, and the
    page it was merged into takes its name as an alias.

    Only an emptied page whose slug is now an alias of a page that's still there goes; any
    other emptied file stays for lint to fail (an empty page no merge explains, a dotfile, a
    `CLAUDE.md`), so the session is resumed to fix it, or the run is discarded.
    """
    merged = {slugify(alias) for page in pages(tree) for alias in page.aliases}
    for folder in PAGE_FOLDERS:
        for entry in folder_files(tree, folder):
            if is_page_name(entry.name) and entry.stem in merged and not entry.read_bytes().strip():
                entry.unlink()


def as_json(value: object) -> str:
    """`value` as the JSON a prompt lists it in."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def commit_fields(message: str) -> dict[str, str]:
    """The `Field: value` lines of a commit message's last paragraph."""
    fields: dict[str, str] = {}
    for line in message.strip().split("\n\n")[-1].splitlines():
        key, separator, value = line.partition(": ")
        if separator and key and " " not in key:
            fields[key] = value.strip()
    return fields


def utc(moment: datetime) -> str:
    """`moment` as a commit message's field holds a time: UTC, ISO 8601."""
    return moment.astimezone(UTC).isoformat()


def figure(value: float | None) -> str:
    """A cost or a count as a commit message's field holds it; `unknown` when the session
    didn't report it."""
    return "unknown" if value is None else f"{value:g}"
