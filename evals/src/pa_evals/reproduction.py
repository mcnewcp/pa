"""A reproduction: fictional raw payloads, wiki pages, and eval questions that fail the way a
case did (ADR-0010).

A diagnostician leaves one for a finding, in its output, and a development session fixes the
fault against it. The diagnostician can't run the librarian or the assistant, so a reproduction
is a hypothesis: it is released marked untested.

This module owns a reproduction's layout, as a session leaves it and as it is released, so
nothing else does. As it is left, in the session's output:

    reproductions/<finding id>/       one directory for each finding that names one
        expectation.md                one line: what should happen, and what happens instead
        questions.json                an eval set (`pa_evals.eval_set`) whose evidence names
                                      payloads by file: `gmail/order.eml`
        payloads/<source>/<file>      raw payloads, in a directory per source (ADR-0004)
        wiki/                         a starting wiki, if the fault needs one, which cites a
                                      payload by file: `{ep:gmail/order.eml}`

As it is released, beside the findings:

    reproductions/<finding id>/
        expectation.md                as it was left
        eval_set.json                 the questions, their evidence resolved to episode ids
        payloads/<source>/<file>      as they were left
        wiki/                         its citations resolved to episode ids, with its index
        reproduction.json             the record that it is untested (`ReproductionRecord`)

A payload's episode id is the one ingest gives it for the synthetic corpus's fictional owner
(`FICTIONAL_OWNER`), whose every reproduction is. So a released reproduction runs as it stands:
`pa-eval run --corpus payloads --eval-set eval_set.json`.

Three things here are for whoever holds a session's output to it:

- `shape_flaws`: what is wrong with the files themselves. Nothing of a reproduction is read
  unless its shape is right;
- `read_reproductions`, then `try_out`: each reproduction put through what a development
  session will put it through, with what that found wrong (`Tried`);
- `Tried.write`: the reproduction as it is released.

Everything a reproduction holds is checked before it is released (`pa_evals.release_check`).
What is wrong with one is private, since it may quote what the session wrote.
"""

from __future__ import annotations

import functools
import re
import stat
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from pa_core.catalog import open_catalog
from pa_core.envelope import Envelope, Source
from pa_core.ingest import inbox_normalizer, ingest
from pa_core.l0 import QuarantineRecord
from pa_core.librarian.index import write_index
from pa_core.librarian.lint import Thresholds, lint
from pa_core.librarian.metadata import L0Metadata
from pa_core.librarian.run import LOOP_AGE_DAYS, PAGE_THRESHOLD
from pa_core.librarian.wiki import DAYS, FOLDERS, INDEX, ROOT_FILES, day_of, is_page_name
from pa_core.normalizers import payload_extension
from pa_core.owner import Owner
from pa_evals.eval_set import CATEGORIES, EvalSet
from pa_evals.models import FrozenModel
from pa_evals.real_content import Passage, words
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0

REPRODUCTIONS = "reproductions"
"""In a session's output, and in a release: the directory of reproductions."""
EXPECTATION = "expectation.md"
QUESTIONS = "questions.json"
"""As it is left: the questions, their evidence named by payload file."""
PAYLOADS = "payloads"
WIKI = "wiki"
EVAL_SET = "eval_set.json"
"""As it is released: the questions as an eval set, their evidence named by episode id."""
RECORD = "reproduction.json"

FICTIONAL_OWNER = Owner(
    name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",)
)
"""The synthetic corpus's fictional owner (`evals/corpus/owner.env`), whose every reproduction
is: nothing in one is the real owner's."""

MOST_PAYLOADS = 40
"""The most raw payloads a reproduction may hold."""
MOST_WIKI_FILES = 20
"""The most files a reproduction's starting wiki may hold."""
MOST_BYTES = 200_000
"""The most bytes a reproduction's files may hold between them. With the other two, it keeps a
reproduction a minimal fixture, never a copy of the case. Constants, not configuration."""
EXPECTATION_CHARACTERS = 400
"""The most characters the line of expectation may hold."""

_FINDING = re.compile(r"f[1-9]\d?")
"""A reproduction's directory is named for its finding, by id."""
_PAYLOAD_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
"""A payload's file name, before its source's extension."""
_CITED_PAYLOAD = re.compile(r"\{ep:([^{}\s]+)\}")
"""A wiki's citation of a payload by file, as a session leaves it."""


@dataclass(frozen=True)
class Flaw:
    """One thing wrong with a reproduction. Private: both fields can quote what the session
    wrote."""

    where: str
    """Which file, from the session's output: `reproductions/f1/payloads/gmail/order.eml`."""
    what: str


@dataclass(frozen=True)
class Reproduction:
    """A reproduction as a session left it, read whole."""

    finding: str
    """The finding it is for, by id: its directory's name."""
    expectation: str
    questions: bytes
    payloads: Mapping[str, bytes]
    """Each payload, by its inbox name: `gmail/order.eml`."""
    wiki: Mapping[str, str]
    """Each file of the starting wiki, by its path in the wiki."""

    @property
    def where(self) -> str:
        """Its directory, from the session's output."""
        return f"{REPRODUCTIONS}/{self.finding}"


class ReproductionRecord(FrozenModel):
    """A released reproduction's `reproduction.json`."""

    finding: str
    tested: bool
    """Whether anyone has run it and seen it fail as its expectation says. Never, on release:
    the diagnostician can't run the librarian or the assistant."""
    payloads: int
    wiki_files: int
    questions: int


@dataclass(frozen=True)
class Text:
    """One file of a reproduction, as the pieces of text a release would hand over in it:
    its name, its lines, and, for a payload, what ingest reads out of it."""

    pieces: tuple[Passage, ...]
    payload: bool = False
    """Whether it is a raw payload, whose format may write a date or a time as one number."""


@dataclass(frozen=True)
class Tried:
    """A reproduction put through what a development session will put it through: its payloads
    ingested into a throwaway data root, and its questions and wiki resolved to the episodes
    they ingest to."""

    reproduction: Reproduction
    envelopes: Mapping[str, Envelope]
    """Each payload that ingests, by its inbox name, as the episode it ingests to."""
    asked: EvalSet | None
    """The questions as they were left; None when they aren't an eval set."""
    eval_set: EvalSet | None
    """The questions, their evidence resolved to episode ids; None when they can't be."""
    wiki: Mapping[str, str]
    """The starting wiki, each citation that names a payload resolved to its episode's id."""
    index: str | None
    """The index a librarian's run would leave the wiki; None when there is no wiki, or when
    its citations can't all be resolved."""
    unsaid: tuple[Flaw, ...]
    """Where it doesn't say what a reproduction says: an expectation that isn't one line,
    questions that aren't an eval set, and evidence or a citation that names no payload of
    its own."""
    not_the_owners: tuple[Flaw, ...]
    """Where it is someone's other than the fictional owner's."""
    quarantined: tuple[Flaw, ...]
    """Each payload that ingest quarantined, or left, with ingest's reason."""
    unlinted: tuple[Flaw, ...]
    """Each error lint found in the wiki, against the episodes the payloads ingest to."""

    @property
    def record(self) -> ReproductionRecord:
        return ReproductionRecord(
            finding=self.reproduction.finding,
            tested=False,
            payloads=len(self.reproduction.payloads),
            wiki_files=len(self.reproduction.wiki),
            questions=0 if self.eval_set is None else len(self.eval_set.questions),
        )

    @functools.cached_property
    def texts(self) -> tuple[Text, ...]:
        """Everything a release would hand over, file by file, as text to check.

        The wiki is read with its citations resolved, as it is released. The questions are
        read field by field, as the eval set is written afresh from them. A payload is read
        twice: as it is written, and as ingest reads it, wherever that says something its
        lines don't (a body in another encoding, a name split over a folded line).
        """
        left, where = self.reproduction, self.reproduction.where
        texts = [Text((Passage(f"{where}/{EXPECTATION}", left.expectation),))]
        if self.asked is not None:
            texts.append(Text(tuple(_asked(f"{where}/{QUESTIONS}", self.asked))))
        for name, raw in left.payloads.items():
            file, written = f"{where}/{PAYLOADS}/{name}", raw.decode()
            pieces = [*_file(file, written)]
            if name in self.envelopes:
                pieces += _as_ingest_reads(file, self.envelopes[name], written)
            texts.append(Text(tuple(pieces), payload=True))
        texts += [
            Text(tuple(_file(f"{where}/{WIKI}/{path}", text))) for path, text in self.wiki.items()
        ]
        return tuple(texts)

    def write(self, directory: Path) -> None:
        """Write the reproduction to `directory` as it is released."""
        if self.eval_set is None or (self.wiki and self.index is None):
            raise ValueError("a reproduction that can't be resolved is never released")
        files: dict[str, bytes] = {
            EXPECTATION: self.reproduction.expectation.encode(),
            EVAL_SET: (self.eval_set.model_dump_json(indent=2) + "\n").encode(),
            RECORD: (self.record.model_dump_json(indent=2) + "\n").encode(),
            **{f"{PAYLOADS}/{name}": raw for name, raw in self.reproduction.payloads.items()},
            **{f"{WIKI}/{path}": text.encode() for path, text in self.wiki.items()},
        }
        if self.index is not None:
            files[f"{WIKI}/{INDEX}"] = self.index.encode()
        for path, data in files.items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def reproduction_values() -> dict[str, str]:
    """What the diagnostician's instructions say of a reproduction that is decided here: the
    caps, the fictional owner, and the categories a question may have."""
    return {
        "most_payloads": str(MOST_PAYLOADS),
        "most_wiki_files": str(MOST_WIKI_FILES),
        "reproduction_bytes": f"{MOST_BYTES:,}",
        "expectation_characters": str(EXPECTATION_CHARACTERS),
        "owner_name": FICTIONAL_OWNER.name,
        "owner_address": FICTIONAL_OWNER.identifier,
        "owner_other_name": ", ".join(FICTIONAL_OWNER.other_names),
        "categories": ", ".join(f"`{category}`" for category in CATEGORIES),
    }


# The shape: what a reproduction's directory may hold.


def shape_flaws(output: Path) -> list[Flaw]:
    """What is wrong with the shape of the reproductions in a session's `output`.

    The directory of reproductions holds a directory for each, named for its finding. Each
    holds the files of the layout and nothing else: regular files of UTF-8 text, of the kinds
    their places take, within the caps. A link is never followed, and nothing is read that is
    over a cap."""
    top = output / REPRODUCTIONS
    if not top.exists(follow_symlinks=False):
        return []
    if top.is_symlink() or not top.is_dir():
        return [Flaw(REPRODUCTIONS, "isn't a directory")]
    flaws: list[Flaw] = []
    for entry in sorted(top.iterdir()):
        where = f"{REPRODUCTIONS}/{entry.name}"
        if _is_directory(entry) and _FINDING.fullmatch(entry.name):
            flaws += _shape(entry, where)
        else:
            flaws.append(_misplaced(entry, where))
    return flaws


def reproductions_left(output: Path) -> list[str]:
    """The findings a session's `output` holds a reproduction for, by id."""
    top = output / REPRODUCTIONS
    if top.is_symlink() or not top.is_dir():
        return []
    return sorted(
        entry.name
        for entry in top.iterdir()
        if _is_directory(entry) and _FINDING.fullmatch(entry.name)
    )


def is_left(output: Path, finding: str) -> bool:
    """Whether a session's `output` holds anything in the place of `finding`'s reproduction.
    Something there that isn't a reproduction is a flaw of shape."""
    return (output / REPRODUCTIONS / finding).exists(follow_symlinks=False)


def read_reproductions(output: Path) -> list[Reproduction]:
    """Every reproduction in a session's `output`, by its finding's id. None of them is read
    unless the shape of all of them is right (`shape_flaws`)."""
    if shape_flaws(output):
        return []
    return [_read(output / REPRODUCTIONS / finding) for finding in reproductions_left(output)]


def _shape(directory: Path, where: str) -> list[Flaw]:
    """What is wrong with the shape of the reproduction in `directory`."""
    flaws: list[Flaw] = []
    payloads: list[Path] = []
    wiki: list[Path] = []
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        here = f"{where}/{entry.name}"
        if entry.name in (EXPECTATION, QUESTIONS) and _is_file(entry):
            files.append(entry)
        elif entry.name == PAYLOADS and _is_directory(entry):
            flaws += _payloads(entry, here, payloads)
        elif entry.name == WIKI and _is_directory(entry):
            flaws += _wiki(entry, here, wiki)
        else:
            flaws.append(_misplaced(entry, here))
    files += payloads + wiki
    size = sum(file.lstat().st_size for file in files)
    if len(payloads) > MOST_PAYLOADS:
        over = f"{len(payloads)} payloads, over the cap of {MOST_PAYLOADS}"
        flaws.append(Flaw(f"{where}/{PAYLOADS}", over))
    if len(wiki) > MOST_WIKI_FILES:
        flaws.append(
            Flaw(f"{where}/{WIKI}", f"{len(wiki)} files, over the cap of {MOST_WIKI_FILES}")
        )
    if size > MOST_BYTES:
        flaws.append(Flaw(where, f"{size} bytes, over the cap of {MOST_BYTES}"))
    else:
        flaws += [
            Flaw(f"{where}/{file.relative_to(directory).as_posix()}", "not UTF-8 text")
            for file in files
            if not _is_text(file)
        ]
    flaws += [
        Flaw(f"{where}/{name}", "missing")
        for name in (EXPECTATION, QUESTIONS)
        if not (directory / name).exists(follow_symlinks=False)
    ]
    if not payloads and not (directory / PAYLOADS).is_symlink():
        flaws.append(Flaw(f"{where}/{PAYLOADS}", "missing: a reproduction has a payload"))
    return flaws


def _payloads(directory: Path, where: str, found: list[Path]) -> Iterator[Flaw]:
    """Flaws in the payloads' directory: a directory per source, each holding only payloads
    in that source's format. The payloads go into `found`."""
    for source in sorted(directory.iterdir()):
        here = f"{where}/{source.name}"
        if source.name not in set(Source) or not _is_directory(source):
            yield _misplaced(source, here)
            continue
        extension = payload_extension(Source(source.name))
        for entry in sorted(source.iterdir()):
            named = entry.name.endswith(extension) and _PAYLOAD_STEM.fullmatch(
                entry.name.removesuffix(extension)
            )
            if named and _is_file(entry):
                found.append(entry)
            else:
                yield _misplaced(entry, f"{here}/{entry.name}")


def _wiki(directory: Path, where: str, found: list[Path]) -> Iterator[Flaw]:
    """Flaws in the starting wiki: the wiki's layout, without the index, which a release
    writes. Lint holds what the files say. The files go into `found`."""
    for entry in sorted(directory.iterdir()):
        here = f"{where}/{entry.name}"
        if entry.name in ROOT_FILES and _is_file(entry):
            found.append(entry)
        elif entry.name in FOLDERS and _is_directory(entry):
            for file in sorted(entry.iterdir()):
                named = day_of(file.name) if entry.name == DAYS else is_page_name(file.name)
                if named and _is_file(file):
                    found.append(file)
                else:
                    yield _misplaced(file, f"{here}/{file.name}")
        else:
            yield _misplaced(entry, here)


def _misplaced(entry: Path, where: str) -> Flaw:
    mode = entry.lstat().st_mode
    if stat.S_ISLNK(mode):
        return Flaw(where, "a link in place of a file")
    if stat.S_ISDIR(mode):
        return Flaw(where, "an unexpected directory")
    return Flaw(where, "an unexpected file" if stat.S_ISREG(mode) else "not a regular file")


def _is_file(path: Path) -> bool:
    return stat.S_ISREG(path.lstat().st_mode)


def _is_directory(path: Path) -> bool:
    return stat.S_ISDIR(path.lstat().st_mode)


def _is_text(path: Path) -> bool:
    try:
        path.read_bytes().decode()
    except UnicodeDecodeError:
        return False
    return True


def _read(directory: Path) -> Reproduction:
    """The reproduction in `directory`, whose shape is right."""
    wiki = directory / WIKI
    return Reproduction(
        finding=directory.name,
        expectation=(directory / EXPECTATION).read_text(encoding="utf-8"),
        questions=(directory / QUESTIONS).read_bytes(),
        payloads={
            path.relative_to(directory / PAYLOADS).as_posix(): path.read_bytes()
            for path in sorted((directory / PAYLOADS).glob("*/*"))
        },
        wiki={
            path.relative_to(wiki).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(wiki.rglob("*"))
            if path.is_file()
        },
    )


# Trying one out.


def try_out(reproduction: Reproduction, *, owner: Owner = FICTIONAL_OWNER) -> Tried:
    """`reproduction`, ingested into a throwaway data root as `owner`'s, with its questions'
    evidence and its wiki's citations resolved to the episodes its payloads ingest to."""
    unsaid = list(_expectation_flaws(reproduction))
    asked, unread = _questions(reproduction)
    unsaid += unread
    as_of = datetime.now(UTC) if asked is None else asked.as_of
    envelopes = _envelopes(reproduction, owner, as_of)
    ids = {name: envelope.episode_id for name, envelope in envelopes.items()}
    wiki, uncited = _cited(reproduction, ids)
    unsaid += uncited
    index, unlinted = None, []
    with tempfile.TemporaryDirectory(prefix="pa-reproduction-") as directory:
        data_root = DataRoot(Path(directory))
        for name, raw in reproduction.payloads.items():
            (data_root.inbox / name).parent.mkdir(parents=True, exist_ok=True)
            (data_root.inbox / name).write_bytes(raw)
        store = FilesystemL0(data_root.l0)
        with open_catalog(data_root.catalog, store) as catalog:
            summary = ingest(data_root.inbox, store, catalog, owner, now=lambda: as_of)
        quarantined = [
            *_quarantined(reproduction, data_root.l0),
            *(Flaw(f"{reproduction.where}/{PAYLOADS}", f"left: {why}") for why in summary.left),
        ]
        ingests = not quarantined and ids.keys() == reproduction.payloads.keys()
        # A wiki is linted against episodes, so only once every payload is one.
        if wiki and ingests and not uncited:
            for path, text in wiki.items():
                (data_root.wiki / path).parent.mkdir(parents=True, exist_ok=True)
                (data_root.wiki / path).write_text(text, encoding="utf-8")
            write_index(data_root.wiki)
            index = (data_root.wiki / INDEX).read_text(encoding="utf-8")
            report = lint(
                data_root.wiki,
                l0=L0Metadata.of(store.envelopes()),
                owner=owner,
                thresholds=Thresholds(as_of.date(), PAGE_THRESHOLD, LOOP_AGE_DAYS),
            )
            unlinted = [
                Flaw(
                    f"{reproduction.where}/{WIKI}/{error.path}"
                    + ("" if error.line is None else f":{error.line}"),
                    error.message,
                )
                for error in report.errors
            ]
    eval_set, not_the_owners = None, []
    if asked is not None:
        unnamed = list(_evidence_flaws(reproduction, asked))
        unsaid += unnamed
        if not unnamed and ingests:
            eval_set = _resolved(asked, ids)
        if asked.owner.to_owner() != owner:
            not_the_owners.append(
                Flaw(
                    f"{reproduction.where}/{QUESTIONS}",
                    "owner: isn't the synthetic corpus's fictional owner, whose every "
                    "reproduction is",
                )
            )
    return Tried(
        reproduction,
        envelopes,
        asked,
        eval_set,
        wiki,
        index,
        unsaid=tuple(unsaid),
        not_the_owners=tuple(not_the_owners),
        quarantined=tuple(quarantined),
        unlinted=tuple(unlinted),
    )


def _envelopes(reproduction: Reproduction, owner: Owner, as_of: datetime) -> dict[str, Envelope]:
    """Each payload that can be an episode, as the envelope ingest makes of it for `owner`.
    A payload's episode id is its envelope's, whenever it is captured."""
    envelopes: dict[str, Envelope] = {}
    for name, raw in reproduction.payloads.items():
        try:
            envelopes[name] = inbox_normalizer(PurePosixPath(name), owner)(raw, captured_at=as_of)
        except Exception:
            continue  # Ingest quarantines it, and says why.
    return envelopes


def _quarantined(reproduction: Reproduction, l0: Path) -> Iterator[Flaw]:
    """Each payload ingest quarantined into the store at `l0`, with its reason."""
    for path in sorted((l0 / "quarantine").glob("*.json")):
        if not path.with_suffix("").is_file():
            continue  # A quarantined payload that is itself JSON, not its record.
        record = QuarantineRecord.model_validate_json(path.read_bytes())
        yield Flaw(
            f"{reproduction.where}/{PAYLOADS}/{record.inbox_name}", f"quarantined: {record.reason}"
        )


def _expectation_flaws(reproduction: Reproduction) -> Iterator[Flaw]:
    where = f"{reproduction.where}/{EXPECTATION}"
    lines = [line for line in reproduction.expectation.splitlines() if line.strip()]
    if len(lines) != 1:
        yield Flaw(where, f"{len(lines)} lines, where one says what should happen and what does")
    elif len(lines[0]) > EXPECTATION_CHARACTERS:
        yield Flaw(where, f"{len(lines[0])} characters, over the cap of {EXPECTATION_CHARACTERS}")


def _questions(reproduction: Reproduction) -> tuple[EvalSet | None, list[Flaw]]:
    """The questions as they were left, their evidence still naming payloads; None, with why,
    when they aren't an eval set."""
    try:
        return EvalSet.model_validate_json(reproduction.questions), []
    except ValidationError as error:
        return None, [
            Flaw(
                f"{reproduction.where}/{QUESTIONS}",
                f"{'.'.join(str(part) for part in problem['loc']) or 'the whole'}: "
                f"{problem['msg']}",
            )
            for problem in error.errors(include_input=False, include_url=False)
        ]


def _evidence_flaws(reproduction: Reproduction, asked: EvalSet) -> Iterator[Flaw]:
    """Each piece of evidence, and each adversarial episode, that names no payload of the
    reproduction."""
    named = [
        *(
            (f"questions.{index}.evidence", name)
            for index, question in enumerate(asked.questions)
            for name in question.evidence
        ),
        *(("adversarial", name) for name in asked.adversarial),
    ]
    for field, name in named:
        if name not in reproduction.payloads:
            yield Flaw(
                f"{reproduction.where}/{QUESTIONS}",
                f"{field}: {name!r} isn't a payload of the reproduction: evidence names a "
                f"payload by its file under {PAYLOADS}/",
            )


def _resolved(asked: EvalSet, ids: Mapping[str, str]) -> EvalSet:
    """`asked`, with each payload it names as the id of the episode the payload ingests to."""
    questions = [
        question.model_copy(update={"evidence": [ids[name] for name in question.evidence]})
        for question in asked.questions
    ]
    adversarial = [ids[name] for name in asked.adversarial]
    return asked.model_copy(update={"questions": questions, "adversarial": adversarial})


def _cited(reproduction: Reproduction, ids: Mapping[str, str]) -> tuple[dict[str, str], list[Flaw]]:
    """The starting wiki with each citation of a payload as a citation of the episode it
    ingests to, and each citation that names no payload of the reproduction."""
    flaws: list[Flaw] = []
    wiki: dict[str, str] = {}
    for path, text in reproduction.wiki.items():
        lines = text.splitlines(keepends=True)
        for number, line in enumerate(lines, start=1):
            for name in _CITED_PAYLOAD.findall(line):
                if name not in reproduction.payloads:
                    flaws.append(
                        Flaw(
                            f"{reproduction.where}/{WIKI}/{path}:{number}",
                            f"cites {name!r}, which isn't a payload of the reproduction",
                        )
                    )
        # A citation that can't be resolved is a flaw already; what it names is no one's text.
        wiki[path] = _CITED_PAYLOAD.sub(
            lambda match: f"[ep:{ids[match[1]]}]" if match[1] in ids else "{ep:}", text
        )
    return wiki, flaws


# What a reproduction says, as text to check.


def _file(file: str, text: str) -> Iterator[Passage]:
    """A file's name, without its directory and its extension, and each of its lines."""
    yield Passage(f"{file}: its name", PurePosixPath(file).stem)
    for number, line in enumerate(text.splitlines(), start=1):
        yield Passage(f"{file}:{number}", line)


def _asked(file: str, asked: EvalSet) -> Iterator[Passage]:
    """Every field of the questions that a session wrote freely."""
    yield Passage(f"{file}: owner.name", asked.owner.name)
    for address in asked.owner.email_addresses:
        yield Passage(f"{file}: owner.email_addresses", address)
    for name in asked.owner.other_names:
        yield Passage(f"{file}: owner.other_names", name)
    yield Passage(f"{file}: time_zone", asked.time_zone)
    for index, question in enumerate(asked.questions):
        yield Passage(f"{file}: questions.{index}.id", question.id)
        yield Passage(f"{file}: questions.{index}.question", question.question)
        yield Passage(f"{file}: questions.{index}.expected_answer", question.expected_answer)


def _as_ingest_reads(file: str, envelope: Envelope, written: str) -> Iterator[Passage]:
    """What ingest reads out of a payload, wherever the payload's own lines don't hold the
    same words in a row."""
    lines = f" {' '.join(words(written))} "
    read = [
        *(("a participant", person.name or "") for person in envelope.participants),
        *(("a participant's address", person.identifier) for person in envelope.participants),
        ("its subject", envelope.subject or ""),
        ("its body", envelope.body),
        ("its calendar's name", envelope.calendar_name or ""),
        *(("a label", label) for label in envelope.labels),
    ]
    for what, text in read:
        said = " ".join(words(text))
        if said and f" {said} " not in lines:
            yield Passage(f"{file}: {what}, as ingest reads it", text)
