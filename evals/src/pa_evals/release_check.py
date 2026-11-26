"""The release check: a diagnosis's findings reach a development session only if every rule
passes (ADR-0010).

A diagnostician's session read real content, and whatever it read could have steered what it
wrote. So its output is checked here, on the devbox, never where the session ran, and released
whole or not at all: one problem under any rule refuses the diagnosis.

A rule is a name and a function from a `Submission` (the diagnosis's output, its case, and the
referral) to the problems it finds. `RULES` lists them, and nothing outside this module knows
what a rule holds. To add one, write its function and add a `Rule` to `RULES`:

- its name is printed, with how many problems it found, by whoever reports the check's outcome;
- each problem says what was found and where, which is private: it goes to the check's notes,
  kept in the diagnosis's directory for the owner alone, and nowhere else.

The first rules hold the output to its form: the files an output may hold, the findings
schema, paths that are in the repository, episodes and questions that are in the case, and
counts of runs under the labels the case gives its sets of runs. The next are the fence: they
refuse real content in every piece of the findings' text (`_pieces`) and in every file of
every reproduction (`pa_evals.reproduction.Trial.texts`), told from fictional by what the case
holds (`pa_evals.real_content`).

- `names`: a name of two or more words that the case holds for a person or an organization.
- `addresses`: an address, a link, or a domain outside the reserved example domains. A
  dotted name the repository or the referral holds is no domain in the findings, unless it is
  written as a link; in a reproduction only the names of its own files are excused.
- `quotes`: six or more words in a row that the case holds, unless the repository or the
  referral holds them too.
- `digits`: a number of seven or more digits, in a row or in the groups a phone number is
  written in. In a reproduction's payloads, a date or a time isn't one.

The last are a reproduction's own. A development session runs one as it stands and commits
it, so it is held to more than the findings are:

- `reproductions`: each finding that names a reproduction has one, and each says what a
  reproduction says.
- `one-word-names`: a name of one word that the case holds, in the letter case it holds it.
- `owner`: its owner is the synthetic corpus's fictional owner.
- `encodings`: all of it can be decoded to text. A payload is released as it was left, so the
  rules above read every decoding of its every part and header (`pa_evals.decodings`), and
  what none of them can read (a part that isn't text, an unknown encoding) refuses it.
- `ingest`: its payloads ingest into a throwaway data root with nothing quarantined.
- `lint`: its wiki lints without errors.

What the case holds comes from its parts (`_parts`), and what is checked is each piece of
text a release would hand over. A case that gains a part adds one there; the rules stay as
they are.

The thresholds are constants, not configuration. The rules catch identifiers and quoted text,
never meaning: a paraphrase passes, and so does a name of one word in the findings.

A release writes the released directory, whole or not at all:

    <released>/<name>/
        findings.json             the findings, as the schema holds them, written afresh
        release.json              the record of the release (`ReleaseRecord`)
        reproductions/<finding>/  each finding's reproduction, as `pa_evals.reproduction`
                                  releases one

Nothing here puts what a rule found in an exception or a return value's printable fields.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_evals.case import Case
from pa_evals.diagnosis import (
    FINDINGS,
    NOTES,
    CaseRecord,
    Diagnosis,
    output_problems,
    read_findings,
)
from pa_evals.findings import Findings, findings_json
from pa_evals.models import FrozenModel
from pa_evals.problem import Problem
from pa_evals.real_content import (
    EPISODE_ID,
    QUOTED_WORDS,
    Envelopes,
    History,
    OwnerIdentity,
    Part,
    Passage,
    RealContent,
    Text,
    Unreserved,
    Wiki,
    Words,
    cased_words,
    held_by,
    holding_dotted_names,
    long_numbers,
    name_runs,
    named,
    repeated,
    repository_passages,
    single_names,
    tracked_files,
    unreserved,
    words,
)
from pa_evals.reproduction import (
    FICTIONAL_OWNER,
    REPRODUCTIONS,
    Trial,
    read_reproductions,
    try_out,
    unmatched,
)
from pa_home.project import REPOSITORY

RELEASED_FINDINGS = "findings.json"
RECORD = "release.json"


class ReleaseError(PaError):
    """A diagnosis that can't be checked, or a release that can't be written. A refusal is not
    an error: it is one of the check's two outcomes."""


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
    reproductions: tuple[Trial, ...] = ()
    """Each reproduction the session left, tried out as a development session will."""

    def public(self) -> Iterator[Passage]:
        """What is public already, so findings may repeat it: the referral, which holds
        nothing real, and the repository."""
        yield Passage("the referral", self.referral)
        yield from repository_passages(self.repository)


@dataclass(frozen=True)
class Reading:
    """Pieces read one after another, as words. A name or a quote is looked for across them,
    so one split between two fields is still found."""

    pieces: Sequence[Passage]
    words: Sequence[str]
    piece_of: Sequence[int]
    """For each word, the index of the piece it is in."""

    @classmethod
    def of(cls, pieces: Sequence[Passage]) -> Reading:
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


def _of_findings(
    check: Callable[[Submission, Findings], Iterable[Problem]],
) -> Callable[[Submission], Iterable[Problem]]:
    """A rule that reads the findings. It has nothing to say of findings that can't be read or
    don't fit the schema: that diagnosis is refused already."""

    def rule(submission: Submission) -> Iterable[Problem]:
        return [] if submission.findings is None else check(submission, submission.findings)

    return rule


def _of_reproductions(
    found: Callable[[Trial], Iterable[Problem]],
) -> Callable[[Submission], Iterable[Problem]]:
    """A rule that hands on what trying each reproduction out found (`pa_evals.reproduction`)."""

    def rule(submission: Submission) -> Iterable[Problem]:
        return [problem for trial in submission.reproductions for problem in found(trial)]

    return rule


def _shape(submission: Submission) -> list[Problem]:
    """The output holds only regular files of the expected names and kinds, within the caps,
    and no links."""
    return output_problems(submission.diagnosis.output)


def _schema(submission: Submission) -> list[Problem]:
    """The findings fit the schema."""
    return [Problem(FINDINGS, problem) for problem in submission.off_schema]


def _paths(submission: Submission, findings: Findings) -> list[Problem]:
    """Every path a finding names is in the repository: a tracked file, or a directory holding
    one."""
    tracked = _tracked(submission.repository)
    return [
        Problem(f"{FINDINGS}: {finding.id}.code", f"{path!r} isn't in the repository")
        for finding in findings.findings
        for path in finding.code
        if not _is_in(path, tracked)
    ]


def _episodes(submission: Submission, findings: Findings) -> list[Problem]:
    """Every episode a finding names is in the case: an id's 16 hex characters could spell
    anything else. And no reproduction names one of them."""
    held = submission.case.cut.episode_ids()
    return [
        *(
            Problem(
                f"{FINDINGS}: {finding.id}.episodes", f"{episode!r} isn't an episode in the case"
            )
            for finding in findings.findings
            for episode in finding.episodes
            if episode not in held
        ),
        # A reproduction names none of them: it becomes a fixture, and their ids stay out.
        *(
            Problem(piece.where, f"{episode!r} is an episode in the case")
            for piece in _reproduced_pieces(submission)
            for episode in EPISODE_ID.findall(piece.text)
            if episode in held
        ),
    ]


_FICTIONAL = OwnerIdentity(FICTIONAL_OWNER)
"""The fictional owner's names, which every reproduction holds and the repository does too:
they pass in a reproduction, whatever holds them in a case."""


def _reproduced_pieces(submission: Submission) -> list[Passage]:
    """Every piece of text of every reproduction, in order."""
    return [
        piece for trial in submission.reproductions for text in trial.texts for piece in text.pieces
    ]


def _reproduced_readings(submission: Submission) -> list[Reading]:
    """Each file of each reproduction, read by itself: a name or a quote is looked for across
    a file's lines, never from one file into the next."""
    return [Reading.of(text.pieces) for trial in submission.reproductions for text in trial.texts]


def _questions(submission: Submission, findings: Findings) -> list[Problem]:
    """Every question a finding names is in the case: in its eval set, or asked in its runs."""
    held = submission.case.question_ids()
    return [
        Problem(f"{FINDINGS}: {finding.id}.questions", f"{question!r} isn't a question in the case")
        for finding in findings.findings
        for question in finding.questions
        if question not in held
    ]


def _labels(submission: Submission, findings: Findings) -> list[Problem]:
    """A finding counts runs only under a label the case gives a set of runs, and never more
    runs than that set holds: a count is a number the session chose."""
    case = submission.case
    problems: list[Problem] = []
    for finding in findings.findings:
        where = f"{FINDINGS}: {finding.id}.runs"
        for label, (_, asked) in finding.runs.items():
            if label not in case.runs:
                problems.append(Problem(where, f"{label!r} isn't a label of a set of runs"))
            elif asked > (held := case.runs_under(label)):
                problems.append(
                    Problem(where, f"{asked} runs asked under {label!r}, which holds {held}")
                )
    return problems


def _pieces(findings: Findings) -> list[Passage]:
    """Every piece of the findings' text that a session wrote freely, in the order the
    released file holds it.

    That is the summary, and each finding's labels with their counts of runs, its mechanism,
    and its suggestion. What is left out is held another way: a field off a fixed list by the
    schema, a finding's id and its questions by their patterns, its episodes by the `episodes`
    rule, and its paths by the `paths` rule.
    """
    pieces = [Passage(f"{FINDINGS}: summary", findings.summary)]
    for finding in findings.findings:
        pieces += [
            Passage(f"{FINDINGS}: {finding.id}.runs", f"{label}: {affected} of {asked}")
            for label, (affected, asked) in finding.runs.items()
        ]
        pieces += [
            Passage(f"{FINDINGS}: {finding.id}.mechanism", finding.mechanism),
            Passage(f"{FINDINGS}: {finding.id}.suggestion", finding.suggestion),
        ]
    return pieces


def _names(submission: Submission, findings: Findings) -> list[Problem]:
    """No name of two or more words that the case holds for a person or an organization: a
    participant's display name, a page's slug, alias, or name, or one of the owner's names.
    In the findings, and in each file of a reproduction, where the fictional owner's own
    names pass."""
    names = submission.real.names
    fictional: set[Words] = set()
    for identifier in _FICTIONAL.identifiers():
        fictional |= name_runs(identifier.text)
    in_a_reproduction = {run: held for run, held in names.items() if run not in fictional}
    read = [
        (Reading.of(_pieces(findings)), names),
        *((reading, in_a_reproduction) for reading in _reproduced_readings(submission)),
    ]
    return [
        Problem(
            reading.where(start),
            f"the name {' '.join(reading.words[start:end])!r}, which the case holds "
            f"({held_by(tuple(reading.words[start:end]), held)})",
        )
        for reading, held in read
        for start, end in named(reading.words, held)
    ]


def _addresses(submission: Submission, findings: Findings) -> list[Problem]:
    """No address, no link, and no domain outside the reserved example domains, whether or not
    the case holds it.

    In the findings, which are prose about code, a dotted name the repository or the referral
    holds is code or a file's name, not a domain. That excuses a name written by itself, never
    an address or a link: the repository names hosts that anyone has pages at.

    In a reproduction nothing the repository holds is excused: it becomes a fixture, where
    every address, link, and domain is a choice. Only the reserved example domains pass, and
    the names of the reproduction's own files (`Trial.file_names`)."""
    found = [(piece, unreserved(piece.text)) for piece in _pieces(findings)]
    dotted = {name for _, outside in found for name in outside.dotted_names}
    held = holding_dotted_names(submission.public(), dotted) if dotted else dotted
    problems = [problem for piece, outside in found for problem in _outside(piece, outside, held)]
    for trial in submission.reproductions:
        problems += [
            problem
            for text in trial.texts
            for piece in text.pieces
            for problem in _outside(piece, unreserved(piece.text), trial.file_names)
        ]
    return problems


def _outside(piece: Passage, outside: Unreserved, excused: Collection[str]) -> Iterator[Problem]:
    """What a piece of text holds outside the reserved example domains: every address, every
    link, and every dotted name but the `excused`."""
    outside_them = "is outside the reserved example domains"
    for address in outside.addresses:
        yield Problem(piece.where, f"the address {address!r} {outside_them}")
    for host in outside.links:
        yield Problem(piece.where, f"the link to {host!r} {outside_them}")
    for name in outside.dotted_names:
        if name not in excused:
            yield Problem(piece.where, f"the domain {name!r} {outside_them}")


def _quotes(submission: Submission, findings: Findings) -> list[Problem]:
    """No six words in a row that the case holds in an episode's subject or body or in a wiki
    file, unless the referral or the repository holds the same six: those are public already.
    Words that go on past six are one quote. In the findings, and in each file of a
    reproduction."""
    readings = [Reading.of(_pieces(findings)), *_reproduced_readings(submission)]
    # The case and the repository are each read once, for every reading's words.
    every = {run for reading in readings for run in _word_runs(reading)}
    held = repeated(submission.real.passages(), every)
    public = repeated(submission.public(), set(held))
    return [problem for reading in readings for problem in _quoted(reading, held, public.keys())]


def _word_runs(reading: Reading) -> list[Words]:
    """Every run of `QUOTED_WORDS` words in `reading`, by the word it starts at."""
    return [
        tuple(reading.words[start : start + QUOTED_WORDS])
        for start in range(len(reading.words) - QUOTED_WORDS + 1)
    ]


def _quoted(reading: Reading, held: dict[Words, str], public: Iterable[Words]) -> list[Problem]:
    """Each quote in `reading`: words in a row that are `held` by the case and aren't
    `public`."""
    runs = _word_runs(reading)
    public = set(public)
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


def _digits(submission: Submission, findings: Findings) -> list[Problem]:
    """No number of seven digits or more, which could be a phone, an account, or a
    confirmation number. An id of one of the case's episodes isn't one, and neither is a
    date. In the findings, and in each file of a reproduction."""
    episodes = submission.case.cut.episode_ids()
    found = [
        (piece.where, number)
        for piece in _pieces(findings)
        for number in long_numbers(piece.text, ids=episodes)
    ]
    for trial in submission.reproductions:
        # In a reproduction, the ids that may be written are its own episodes', which a
        # release writes into its wiki, and a payload may write a time as its format does.
        own = {envelope.episode_id for envelope in trial.envelopes.values()}
        found += [
            (piece.where, number)
            for text in trial.texts
            for piece in text.pieces
            for number in long_numbers(piece.text, ids=own, times=text.payload)
        ]
    return [
        Problem(
            where,
            f"a number of {sum(character.isdigit() for character in number)} digits: {number!r}",
        )
        for where, number in found
    ]


def _reproductions(submission: Submission, findings: Findings) -> list[Problem]:
    """Every finding that names a reproduction has one, every reproduction is named by its
    finding, and each says what a reproduction says: one line of expectation, questions that
    are an eval set, and evidence and citations that name its own payloads."""
    return [
        *unmatched(submission.diagnosis.output, findings.reproduced()),
        *(problem for trial in submission.reproductions for problem in trial.form_problems),
    ]


def _one_word_names(submission: Submission) -> list[Problem]:
    """No reproduction holds a name of one word that the case holds for a person or an
    organization, in the letter case it is a name in. Only a reproduction is held to this:
    every name in one is a choice, where findings are prose about code."""
    names = submission.real.single_names
    fictional = single_names(_FICTIONAL.identifiers())
    return [
        Problem(piece.where, f"the name {word!r}, which the case holds ({names[word]})")
        for piece in _reproduced_pieces(submission)
        for word in cased_words(piece.text)
        if word in names and word not in fictional
    ]


RULES: tuple[Rule, ...] = (
    Rule("shape", _shape),
    Rule("schema", _schema),
    Rule("paths", _of_findings(_paths)),
    Rule("episodes", _of_findings(_episodes)),
    Rule("questions", _of_findings(_questions)),
    Rule("labels", _of_findings(_labels)),
    Rule("names", _of_findings(_names)),
    Rule("addresses", _of_findings(_addresses)),
    Rule("quotes", _of_findings(_quotes)),
    Rule("digits", _of_findings(_digits)),
    Rule("reproductions", _of_findings(_reproductions)),
    Rule("one-word-names", _one_word_names),
    # Every reproduction is the synthetic corpus's fictional owner's, so nothing in one
    # is the real owner's.
    Rule("owner", _of_reproductions(lambda trial: trial.owner_problems)),
    # Everything in every reproduction can be decoded to text, so all of it was read by
    # the rules above: no part of a payload that isn't text, no encoding or character
    # set nobody knows, and no data in a `data:` URI.
    Rule("encodings", _of_reproductions(lambda trial: trial.encoding_problems)),
    # Every reproduction's payloads ingest into a throwaway data root, with nothing
    # quarantined.
    Rule("ingest", _of_reproductions(lambda trial: trial.ingest_problems)),
    # Every reproduction's wiki lints without errors, against the episodes its payloads
    # ingest to.
    Rule("lint", _of_reproductions(lambda trial: trial.lint_problems)),
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
    reproductions: int = 0
    """How many reproductions were released with the findings, each untested."""
    payloads: int = 0
    """How many raw payloads the reproductions hold between them."""
    revisions: int = 0
    """How many times the diagnosis was revised after the check refused it, before this
    release: each time it was held to every rule again."""


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
    referral: str,
    owner: Owner,
    released: Path,
    repository: Path = REPOSITORY,
) -> Release | Refusal:
    """Checks `diagnosis` against every rule, and releases its findings to the directory
    `released` or refuses it.

    `case` is the case the diagnosis read, in the devbox's copy, and `owner` the owner whose
    store it was cut from: what they hold is what the findings may not. `referral` is the
    referral as the devbox asked it, whose words the findings may repeat. It is never read
    from the diagnosis's directory, which came back from where the session ran.

    A release writes `released` whole; a refusal writes nothing there, and keeps what each
    rule found in the diagnosis's directory. Either way the diagnosis's directory is left
    readable by the owner alone.

    Raises ReleaseError, with nothing checked, when `released` is already there (no release is
    written over), when the diagnosis read another case, or when the referral kept with it
    isn't `referral`.
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
    if diagnosis.referral_text() != referral:
        raise ReleaseError(
            f"the referral kept with the diagnosis {diagnosis.name} isn't the referral that was "
            "given: what came back from where the session ran doesn't say what was asked, so "
            "nothing is checked and nothing is released"
        )
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
        reproductions=len(submission.reproductions),
        payloads=sum(len(trial.reproduction.payloads) for trial in submission.reproductions),
        revisions=session.revisions,
    )
    _write(released, findings, submission.reproductions, record)
    return Release(released, record)


def _submitted(
    diagnosis: Diagnosis, case: Case, referral: str, repository: Path, owner: Owner
) -> Submission:
    findings, off_schema = read_findings(diagnosis.output)
    return Submission(
        diagnosis=diagnosis,
        case=case,
        referral=referral,
        repository=repository,
        findings=findings,
        off_schema=off_schema,
        real=RealContent(_parts(case, owner)),
        reproductions=_trials(diagnosis) if findings is not None else (),
    )


def _trials(diagnosis: Diagnosis) -> tuple[Trial, ...]:
    """Each reproduction in the diagnosis's output, tried out."""
    return tuple(try_out(reproduction) for reproduction in read_reproductions(diagnosis.output))


def _parts(case: Case, owner: Owner) -> list[Part]:
    """The parts of `case` that hold real content, and the owner's identity. A case that
    gains a part gains one here."""
    parts: list[Part] = [OwnerIdentity(owner), Envelopes(case.cut.l0)]
    if case.cut.wiki is not None:
        parts.append(Wiki(case.cut.wiki))
    for label, wiki in case.wikis.items():
        parts.append(Wiki(wiki.tree, name=label))
        parts.append(History(wiki.history_files(), name=label))
    parts.append(Text(lambda: _text(case)))
    return parts


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


def _write(
    released: Path, findings: Findings, reproductions: Sequence[Trial], record: ReleaseRecord
) -> None:
    """Write the released directory through a hidden one renamed into place, so it is there
    whole or not at all."""
    building = released.parent / f".{released.name}.partial"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    try:
        (building / RELEASED_FINDINGS).write_bytes(findings_json(findings))
        (building / RECORD).write_text(record.model_dump_json(indent=2) + "\n")
        for trial in reproductions:
            trial.write(building / REPRODUCTIONS / trial.reproduction.finding)
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
