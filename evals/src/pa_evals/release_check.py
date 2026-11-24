"""The release check: a diagnosis's findings reach a development session only if every rule
passes (ADR-0010).

A diagnostician's session read real content, and whatever it read could have steered what it
wrote. So its output is checked here, on the devbox, never where the session ran, and released
whole or not at all: one problem under any rule refuses the diagnosis.

A rule is a name and a function from a `Submission` (the diagnosis's output, its case, and the
referral) to the problems it finds. `RULES` lists them, and nothing outside this module knows
what a rule holds. To add one, write its function and add a `Rule` to `RULES`:

- its name is printed, with how many problems it found, by whoever reports the verdict;
- each problem says what was found and where, which is private: it goes to the check's notes,
  kept in the diagnosis's directory for the owner alone, and nowhere else.

The rules here hold the output to its form: the files an output may hold, the findings schema,
and paths that are in the repository. None of them knows real content from fictional yet (a
name, an address, quoted text, a long number), so until those rules are here a release is for a
synthetic case only, and nothing here may be run over real data.

A release writes the released directory, whole or not at all:

    <released>/<name>/
        findings.json             the findings, as the schema holds them, written afresh
        release.json              the record of the release (`ReleaseRecord`)

Nothing here puts what a rule found in an exception or a return value's printable fields.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pa_core.errors import PaError
from pa_evals.diagnosis import FINDINGS, Case, Diagnosis, output_problems, read_output
from pa_evals.findings import Findings, FindingsError, findings_json, parse_findings
from pa_evals.models import FrozenModel
from pa_home.project import REPOSITORY

NOTES = "check-notes.md"
"""In a diagnosis's directory: what the check found, and where, when it refused."""
RELEASED_FINDINGS = "findings.json"
RECORD = "release.json"


class ReleaseError(PaError):
    """A diagnosis that can't be checked, or a release that can't be written. A refusal is not
    an error: it is a verdict."""


@dataclass(frozen=True)
class Problem:
    """One thing a rule found. Private: both fields may quote the session's output."""

    where: str
    what: str


@dataclass(frozen=True)
class Submission:
    """What the rules check: a diagnosis's output, with its case and the referral."""

    diagnosis: Diagnosis
    """The diagnosis, whose `output` is what the session left."""
    case: Case
    referral: str
    repository: Path
    """The checkout the check runs in."""
    findings: Findings | None
    """The findings, or None when they can't be read or don't fit the schema. A rule that
    reads them has nothing to say then: the diagnosis is refused already."""
    off_schema: tuple[str, ...]
    """Where the findings leave the schema, and why."""


@dataclass(frozen=True)
class Rule:
    name: str
    """What the rule is called where its count is printed and recorded."""
    check: Callable[[Submission], Iterable[Problem]]


def _shape(submission: Submission) -> list[Problem]:
    """The output holds only regular files of the expected names and kinds, within the caps,
    and no links."""
    return [
        Problem(problem.path, problem.what)
        for problem in output_problems(submission.diagnosis.output)
    ]


def _schema(submission: Submission) -> list[Problem]:
    """The findings fit the schema."""
    return [Problem(FINDINGS, problem) for problem in submission.off_schema]


def _paths(submission: Submission) -> list[Problem]:
    """Every path a finding names is in the repository: a tracked file, or a directory holding
    one."""
    if submission.findings is None:
        return []
    tracked = _tracked(submission.repository)
    return [
        Problem(f"{FINDINGS}: {finding.id}.code", f"{path!r} isn't in the repository")
        for finding in submission.findings.findings
        for path in finding.code
        if not _is_in(path, tracked)
    ]


RULES: tuple[Rule, ...] = (
    Rule("shape", _shape),
    Rule("schema", _schema),
    Rule("paths", _paths),
)
"""Every rule, in the order its count is reported."""


class ReleaseRecord(FrozenModel):
    """A release's `release.json`: what was released, and what produced it."""

    name: str
    """The diagnosis's name."""
    cut: str
    """The name of the cut the diagnosis read."""
    commit: str
    """The commit of the code that ran the diagnosis's session."""
    findings: int
    kinds: dict[str, int]
    """How many findings there are of each kind."""
    checks: dict[str, int]
    """Each rule, with how many problems it found."""


@dataclass(frozen=True)
class Release:
    """A diagnosis whose findings passed every rule, and were released."""

    directory: Path
    """The released directory."""
    record: ReleaseRecord


@dataclass(frozen=True)
class Refusal:
    """A diagnosis that failed a rule: nothing of it was released."""

    checks: dict[str, int]
    """Each rule, with how many problems it found."""
    notes: Path
    """What was found, and where: private."""


def release(
    diagnosis: Diagnosis, case: Case, *, released: Path, repository: Path = REPOSITORY
) -> Release | Refusal:
    """Checks `diagnosis` against every rule, and releases its findings to the directory
    `released` or refuses it.

    `case` is the case the diagnosis read, in the devbox's copy. A release writes `released`
    whole; a refusal writes nothing there, and keeps what each rule found in the diagnosis's
    directory. Either way the diagnosis's directory is left readable by the owner alone.

    Raises ReleaseError when `released` is already there (no release is written over), or when
    the diagnosis read another case.
    """
    session = diagnosis.session()
    if session.name != diagnosis.name:
        raise ReleaseError(
            f"{diagnosis.path} holds a diagnosis of another name, so it can't be released as "
            f"{diagnosis.name}"
        )
    if session.cut != case.cut.name:
        raise ReleaseError(
            f"the diagnosis {diagnosis.name} read the cut {session.cut}, not {case.cut.name}: "
            "it is checked against the case it read"
        )
    if released.exists(follow_symlinks=False):
        raise ReleaseError(
            f"a diagnosis named {diagnosis.name} is already released in {released.parent}: no "
            "release is written over"
        )
    referral = diagnosis.referral_text()
    diagnosis.make_private()
    submission = _submitted(diagnosis, case, referral, repository)
    found = {rule.name: list(rule.check(submission)) for rule in RULES}
    checks = {name: len(problems) for name, problems in found.items()}
    findings = submission.findings
    if findings is None or any(checks.values()):
        return Refusal(checks, diagnosis.keep(NOTES, _notes(diagnosis.name, found)))
    record = ReleaseRecord(
        name=diagnosis.name,
        cut=session.cut,
        commit=session.commit,
        findings=len(findings.findings),
        kinds=findings.kinds(),
        checks=checks,
    )
    _write(released, findings, record)
    return Release(released, record)


def _submitted(diagnosis: Diagnosis, case: Case, referral: str, repository: Path) -> Submission:
    findings, off_schema = None, ()
    data = read_output(diagnosis.output, FINDINGS)
    if data is not None:
        try:
            findings = parse_findings(data)
        except FindingsError as error:
            off_schema = error.problems
    return Submission(
        diagnosis=diagnosis,
        case=case,
        referral=referral,
        repository=repository,
        findings=findings,
        off_schema=off_schema,
    )


def _notes(name: str, found: dict[str, list[Problem]]) -> str:
    """The check's notes on a refusal: every rule, and what it found where."""
    lines = [
        f"# The release check's notes: {name}",
        "",
        "Refused, and nothing was released. This is what each rule found, and where. It quotes "
        "the session's output, so it is the owner's alone.",
    ]
    for rule, problems in found.items():
        lines += ["", f"## {rule}: {len(problems)}", ""]
        lines += [f"- {problem.where}: {problem.what}" for problem in problems] or ["Nothing."]
    return "\n".join(lines) + "\n"


def _write(released: Path, findings: Findings, record: ReleaseRecord) -> None:
    """Write the released directory through a hidden one renamed into place, so it is there
    whole or not at all."""
    building = released.parent / f".{released.name}.partial"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    try:
        (building / RELEASED_FINDINGS).write_bytes(findings_json(findings))
        (building / RECORD).write_text(record.model_dump_json(indent=2) + "\n")
        building.rename(released)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise


def _tracked(repository: Path) -> frozenset[str]:
    """Every file git tracks in `repository`, and every directory holding one, as paths from
    its root."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raise ReleaseError(
            f"can't list the files git tracks in {repository}: the release check runs in a "
            "checkout of the repository"
        ) from None
    files = [PurePosixPath(name) for name in listed.split("\0") if name]
    return frozenset(str(path) for file in files for path in (file, *file.parents) if path.parts)


def _is_in(path: str, tracked: frozenset[str]) -> bool:
    """Whether `path` names something in the repository, from its root: never an absolute
    path, and never one that climbs out."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return False
    return str(pure) in tracked
