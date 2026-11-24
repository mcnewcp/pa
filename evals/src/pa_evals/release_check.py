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

The first rules hold the output to its form: the files an output may hold, the findings
schema, paths that are in the repository, episodes and questions that are in the case, and
counts of runs under the labels the case gives its sets of runs. The rest are the
fence: they refuse real content in every piece of the findings' text (`_pieces`), told from
fictional by what the case holds (`pa_evals.real_content`).

- `names`: a name of two or more words that the case holds for a person or an organization.
- `addresses`: an address or a domain outside the reserved example domains.
- `quotes`: six or more words in a row that the case holds, unless the repository or the
  referral holds them too.
- `digits`: a number of seven or more digits, in a row or in the groups a phone number is
  written in.

What the case holds comes from its parts (`_sources`), and what is checked is each piece of
text a release would hand over (`_pieces`). A diagnosis that gains an output (a reproduction)
adds its pieces, and a case that gains a part adds a source; the rules stay as they are.

The thresholds are constants, not configuration. The rules catch identifiers and quoted text,
never meaning: a paraphrase passes, and so does a name of one word.

A release writes the released directory, whole or not at all:

    <released>/<name>/
        findings.json             the findings, as the schema holds them, written afresh
        release.json              the record of the release (`ReleaseRecord`)

Nothing here puts what a rule found in an exception or a return value's printable fields.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_evals.case import Case
from pa_evals.diagnosis import (
    FINDINGS,
    CaseRecord,
    Diagnosis,
    output_problems,
    read_output,
)
from pa_evals.findings import Findings, FindingsError, findings_json, parse_findings
from pa_evals.models import FrozenModel
from pa_evals.real_content import (
    QUOTED_WORDS,
    Envelopes,
    History,
    OwnerIdentity,
    Passage,
    RealContent,
    Source,
    Text,
    Wiki,
    held_by,
    holding_dotted_names,
    long_numbers,
    named,
    repeated,
    repository_passages,
    tracked_files,
    unreserved,
    words,
)
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
    real: RealContent
    """What the case holds, and the owner's identity."""

    def public(self) -> Iterator[Passage]:
        """What is public already, so findings may repeat it: the referral, which holds
        nothing real, and the repository."""
        yield Passage("the referral", self.referral)
        yield from repository_passages(self.repository)


@dataclass(frozen=True)
class Piece:
    """A piece of the text a release would hand a development session."""

    where: str
    """Which file and field it is, for the notes: `findings.json: f1.mechanism`."""
    text: str


@dataclass(frozen=True)
class Reading:
    """Pieces read one after another, as words. A name or a quote is looked for across them,
    so one split between two fields is still found."""

    pieces: Sequence[Piece]
    words: Sequence[str]
    piece_of: Sequence[int]
    """For each word, the index of the piece it is in."""

    @classmethod
    def of(cls, pieces: Sequence[Piece]) -> Reading:
        read = [(word, index) for index, piece in enumerate(pieces) for word in words(piece.text)]
        return cls(pieces, [word for word, _ in read], [index for _, index in read])

    def where(self, word: int) -> str:
        """Where the word at index `word` is."""
        return self.pieces[self.piece_of[word]].where


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


def _episodes(submission: Submission) -> list[Problem]:
    """Every episode a finding names is in the case. An id's 16 hex characters could spell
    anything else."""
    if submission.findings is None:
        return []
    held = submission.case.cut.episode_ids()
    return [
        Problem(f"{FINDINGS}: {finding.id}.episodes", f"{episode!r} isn't an episode in the case")
        for finding in submission.findings.findings
        for episode in finding.episodes
        if episode not in held
    ]


def _questions(submission: Submission) -> list[Problem]:
    """Every question a finding names is in the case: in its eval set, or asked in its runs."""
    if submission.findings is None:
        return []
    held = submission.case.question_ids()
    return [
        Problem(f"{FINDINGS}: {finding.id}.questions", f"{question!r} isn't a question in the case")
        for finding in submission.findings.findings
        for question in finding.questions
        if question not in held
    ]


def _labels(submission: Submission) -> list[Problem]:
    """A finding counts runs only under a label the case gives a set of runs, and never more
    runs than that set holds: a count is a number the session chose."""
    if submission.findings is None:
        return []
    case = submission.case
    problems: list[Problem] = []
    for finding in submission.findings.findings:
        where = f"{FINDINGS}: {finding.id}.runs"
        for label, (_, asked) in finding.runs.items():
            if label not in case.runs:
                problems.append(Problem(where, f"{label!r} isn't a label of a set of runs"))
            elif asked > (held := case.runs_under(label)):
                problems.append(
                    Problem(where, f"{asked} runs asked under {label!r}, which holds {held}")
                )
    return problems


def _pieces(findings: Findings) -> list[Piece]:
    """Every piece of the findings' text that a session wrote freely, in the order the
    released file holds it.

    That is the summary, and each finding's labels with their counts of runs, its mechanism,
    and its suggestion. What is left out is held another way: a field off a fixed list by the
    schema, a finding's id and its questions by their patterns, its episodes by the `episodes`
    rule, and its paths by the `paths` rule.
    """
    pieces = [Piece(f"{FINDINGS}: summary", findings.summary)]
    for finding in findings.findings:
        pieces += [
            Piece(f"{FINDINGS}: {finding.id}.runs", f"{label}: {affected} of {asked}")
            for label, (affected, asked) in finding.runs.items()
        ]
        pieces += [
            Piece(f"{FINDINGS}: {finding.id}.mechanism", finding.mechanism),
            Piece(f"{FINDINGS}: {finding.id}.suggestion", finding.suggestion),
        ]
    return pieces


def _names(submission: Submission) -> list[Problem]:
    """No name of two or more words that the case holds for a person or an organization: a
    participant's display name, a page's slug, alias, or name, or one of the owner's names."""
    if submission.findings is None:
        return []
    reading = Reading.of(_pieces(submission.findings))
    names = submission.real.names
    return [
        Problem(
            reading.where(start),
            f"the name {' '.join(reading.words[start:end])!r}, which the case holds "
            f"({held_by(tuple(reading.words[start:end]), names)})",
        )
        for start, end in named(reading.words, names)
    ]


def _addresses(submission: Submission) -> list[Problem]:
    """No address, and no domain, outside the reserved example domains, whether or not the
    case holds it. A dotted name the repository or the referral holds is code or a file's
    name, not a domain; an address is never excused."""
    if submission.findings is None:
        return []
    found = [(piece, unreserved(piece.text)) for piece in _pieces(submission.findings)]
    dotted = {name for _, outside in found for name in outside.dotted_names}
    domains = dotted - holding_dotted_names(submission.public(), dotted) if dotted else dotted
    outside_them = "is outside the reserved example domains"
    return [
        problem
        for piece, outside in found
        for problem in (
            *(Problem(piece.where, f"the address {a!r} {outside_them}") for a in outside.addresses),
            *(
                Problem(piece.where, f"the domain {name!r} {outside_them}")
                for name in outside.dotted_names
                if name in domains
            ),
        )
    ]


def _quotes(submission: Submission) -> list[Problem]:
    """No six words in a row that the case holds in an episode's subject or body or in a wiki
    file, unless the referral or the repository holds the same six: those are public already.
    Words that go on past six are one quote."""
    if submission.findings is None:
        return []
    reading = Reading.of(_pieces(submission.findings))
    runs = [
        tuple(reading.words[start : start + QUOTED_WORDS])
        for start in range(len(reading.words) - QUOTED_WORDS + 1)
    ]
    held = repeated(submission.real.passages(), set(runs))
    public = repeated(submission.public(), set(held))
    quoted = [run in held and run not in public for run in runs]
    problems: list[Problem] = []
    start = 0
    while start < len(runs):
        if not quoted[start]:
            start += 1
            continue
        last = start
        while last + 1 < len(runs) and quoted[last + 1]:
            last += 1
        end = last + QUOTED_WORDS
        problems.append(
            Problem(
                reading.where(start),
                f"{end - start} words in a row that the case holds ({held[runs[start]]}): "
                f"{' '.join(reading.words[start:end])!r}",
            )
        )
        start = last + 1
    return problems


def _digits(submission: Submission) -> list[Problem]:
    """No number of seven digits or more, which could be a phone, an account, or a
    confirmation number. An id of one of the case's episodes isn't one, and neither is a
    date."""
    if submission.findings is None:
        return []
    episodes = submission.case.cut.episode_ids()
    return [
        Problem(
            piece.where,
            f"a number of {sum(character.isdigit() for character in number)} digits: {number!r}",
        )
        for piece in _pieces(submission.findings)
        for number in long_numbers(piece.text, ids=episodes)
    ]


RULES: tuple[Rule, ...] = (
    Rule("shape", _shape),
    Rule("schema", _schema),
    Rule("paths", _paths),
    Rule("episodes", _episodes),
    Rule("questions", _questions),
    Rule("labels", _labels),
    Rule("names", _names),
    Rule("addresses", _addresses),
    Rule("quotes", _quotes),
    Rule("digits", _digits),
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
    diagnosis: Diagnosis,
    case: Case,
    *,
    owner: Owner,
    released: Path,
    repository: Path = REPOSITORY,
) -> Release | Refusal:
    """Checks `diagnosis` against every rule, and releases its findings to the directory
    `released` or refuses it.

    `case` is the case the diagnosis read, in the devbox's copy, and `owner` the owner whose
    store it was cut from: what they hold is what the findings may not. A release writes `released`
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
    if session.case != CaseRecord.of(case):
        raise ReleaseError(
            f"the diagnosis {diagnosis.name} read a case of other parts than the one given: it "
            f"is checked against the case it read. It read: {session.case.describe()}. Given: "
            f"{CaseRecord.of(case).describe()}"
        )
    if released.exists(follow_symlinks=False):
        raise ReleaseError(
            f"a diagnosis named {diagnosis.name} is already released in {released.parent}: no "
            "release is written over"
        )
    referral = diagnosis.referral_text()
    diagnosis.make_private()
    submission = _submitted(diagnosis, case, referral, repository, owner)
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


def _submitted(
    diagnosis: Diagnosis, case: Case, referral: str, repository: Path, owner: Owner
) -> Submission:
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
        real=RealContent(_sources(case, owner)),
    )


def _sources(case: Case, owner: Owner) -> list[Source]:
    """The parts of `case` that hold real content, and the owner's identity. A case that
    gains a part gains a source here."""
    sources: list[Source] = [OwnerIdentity(owner), Envelopes(case.cut.l0)]
    if case.cut.wiki is not None:
        sources.append(Wiki(case.cut.wiki))
    for label, wiki in case.wikis.items():
        sources.append(Wiki(wiki.tree, name=label))
        sources.append(History(wiki.history_files(), name=label))
    sources.append(Text(lambda: _text(case)))
    return sources


def _text(case: Case) -> Iterator[Passage]:
    """The text of the case's parts that are text alone: the eval set's questions and
    expected answers, each run's answers, errors, and judge's reasoning, and the earlier
    diagnoses' private analyses."""
    eval_set = case.read_eval_set()
    for question in eval_set.questions if eval_set else ():
        yield Passage(f"the eval set's question {question.id}", question.question)
        yield Passage(f"the eval set's expected answer to {question.id}", question.expected_answer)
    for label in case.runs:
        for number, run in enumerate(case.results(label), start=1):
            for asked in run.questions:
                of = f"{asked.id} in run {number} under {label}"
                yield Passage(f"the question {of}", asked.question)
                yield Passage(f"the expected answer to {of}", asked.expected_answer)
                yield Passage(f"the answer to {of}", asked.answer or "")
                yield Passage(f"the judge's reasoning on {of}", asked.judge_reasoning or "")
                yield Passage(f"the error on {of}", asked.error or "")
    for name, analysis in case.earlier.items():
        yield Passage(
            f"the earlier diagnosis {name}'s analysis",
            analysis.read_text(encoding="utf-8", errors="replace"),
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
    files = [PurePosixPath(name) for name in tracked_files(repository)]
    return frozenset(str(path) for file in files for path in (file, *file.parents) if path.parts)


def _is_in(path: str, tracked: frozenset[str]) -> bool:
    """Whether `path` names something in the repository, from its root: never an absolute
    path, and never one that climbs out."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return False
    return str(pure) in tracked
