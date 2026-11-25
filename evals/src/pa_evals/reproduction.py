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
        reproduction.json             the record that it is untested, and of the episode each
                                      payload ingests to (`ReproductionRecord`)

A payload's episode id is the one ingest gives it for the synthetic corpus's fictional owner
(`FICTIONAL_OWNER`), whose every reproduction is. So a released reproduction is run as a
reproduction, by its directory (`read_released` gives a command its parts):

- `pa-eval run --reproduction DIR` asks its questions over its payloads, and with
  `--configuration files-plus-wiki` over its starting wiki, which needs no run log;
- `pa-eval reproduction dream DIR COPY` dreams a copy of its starting wiki, as its eval set's
  owner and as of its eval set's as-of time, and `pa-eval run --reproduction DIR --wiki COPY`
  asks with that copy.

The release refuses a starting wiki that fails lint (`Trial.lint_problems`), so a fault that
starts from a page lint fails can't be released as a reproduction: its finding describes the
state instead.

A development session renames or trims a released reproduction before it commits it, and an
edited payload can ingest to another episode. `resolve_again` puts the questions and the wiki
right after such an edit (`pa-eval reproduction resolve`).

What is here is for whoever holds a session's output to it:

- `shape_problems`: what is wrong with the files themselves. Nothing of a reproduction is read
  unless its shape is right;
- `read_reproductions`, then `try_out`: each reproduction put through what a development
  session will put it through, with what that found wrong (`Trial`);
- `problems_in_form`: everything above that can be told without the case, which the session that
  left a reproduction is resumed to put right (`pa_evals.diagnosis.out_of_form`);
- `Trial.write`: the reproduction as it is released.

Everything a reproduction holds is checked before it is released (`pa_evals.release_check`),
against the case too, on the devbox. What is wrong with one is private, since it may quote
what the session wrote.
"""

from __future__ import annotations

import functools
import re
import shutil
import stat
import tempfile
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from pa_core.catalog import open_catalog
from pa_core.envelope import Envelope, Source
from pa_core.errors import PaError
from pa_core.ingest import inbox_normalizer, ingest
from pa_core.l0 import QuarantineRecord
from pa_core.librarian.index import write_index
from pa_core.librarian.lint import Thresholds, lint
from pa_core.librarian.metadata import L0Metadata
from pa_core.librarian.run import LOOP_AGE_DAYS, PAGE_THRESHOLD
from pa_core.librarian.wiki import (
    CITATION,
    DAYS,
    FOLDERS,
    INDEX,
    ROOT_FILES,
    day_of,
    is_page_name,
)
from pa_core.normalizers import payload_extension
from pa_core.owner import Owner
from pa_evals.decodings import (
    Decoded,
    Decoding,
    Said,
    decode_payload,
    embedded_data,
    text_decodings,
)
from pa_evals.eval_set import CATEGORIES, EvalSet
from pa_evals.findings import FINDING_ID
from pa_evals.models import FrozenModel, problems_of
from pa_evals.plain_files import is_text, regular_files
from pa_evals.problem import Problem
from pa_evals.real_content import Passage
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

_FINDING = re.compile(FINDING_ID)
"""A reproduction's directory is named for its finding, by id."""
_PAYLOAD_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
"""A payload's file name, before its source's extension."""
_CITED_PAYLOAD = re.compile(r"\{ep:([^{}\s]+)\}")
"""A wiki's citation of a payload by file, as a session leaves it."""


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
    episodes: dict[str, str]
    """The episode each payload ingests to, by the payload's inbox name: what the questions'
    evidence and the wiki's citations were resolved to. An edit to a payload can change its
    episode's id, and this is what tells `resolve_again` which payload an id was."""


@dataclass(frozen=True)
class FileText:
    """One file of a reproduction, as the pieces of text a release would hand over in it:
    its name, its lines, and every other way it reads (`pa_evals.decodings`): each decoding
    of it that says something its lines don't."""

    pieces: tuple[Passage, ...]
    payload: bool = False
    """Whether it is a raw payload, whose format may write a date or a time as one number."""


@dataclass(frozen=True)
class Trial:
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
    form_problems: tuple[Problem, ...]
    """Where it doesn't say what a reproduction says: an expectation that isn't one line,
    questions that aren't an eval set, and evidence or a citation that names no payload of
    its own."""
    owner_problems: tuple[Problem, ...]
    """Where it is someone's other than the fictional owner's."""
    ingest_problems: tuple[Problem, ...]
    """Each payload that ingest quarantined, or left, with ingest's reason."""
    lint_problems: tuple[Problem, ...]
    """Each error lint found in the wiki, against the episodes the payloads ingest to."""
    decoded: Mapping[str, Decoded]
    """Each payload, by its inbox name, decoded every way its format can be."""

    @property
    def problems(self) -> tuple[Problem, ...]:
        """Everything trying it out found wrong with it."""
        return (
            *self.form_problems,
            *self.owner_problems,
            *self.encoding_problems,
            *self.ingest_problems,
            *self.lint_problems,
        )

    @functools.cached_property
    def file_names(self) -> frozenset[str]:
        """The names of its own files, in lower case, which it may cite (`order.eml`): its
        payloads', each with the name of the file a note says it was captured from, and its
        wiki's, with those a release writes. Dotted names that are no domains."""
        files = [
            *self.reproduction.payloads,
            *(
                envelope.native_id
                for envelope in self.envelopes.values()
                if envelope.source == Source.OBSIDIAN
            ),
            *self.reproduction.wiki,
            *(EXPECTATION, QUESTIONS, EVAL_SET, RECORD, INDEX),
        ]
        return frozenset(PurePosixPath(file).name.lower() for file in files)

    @functools.cached_property
    def encoding_problems(self) -> tuple[Problem, ...]:
        """Where it holds something that can't be decoded to text, and so can't be checked:
        a part of a payload that isn't text, or is in an encoding or a character set nobody
        knows, and data carried in a `data:` URI, in any file."""
        payloads = f"{self.reproduction.where}/{PAYLOADS}"
        return (
            *(
                Problem(f"{payloads}/{name}", what)
                for name, decoded in self.decoded.items()
                for what in decoded.unread
            ),
            *(
                Problem(piece.where, f"{count} `data:` URIs that carry their data in base64")
                for text in self.texts
                for piece in text.pieces
                if (count := embedded_data(piece.text))
            ),
        )

    @property
    def record(self) -> ReproductionRecord:
        return ReproductionRecord(
            finding=self.reproduction.finding,
            tested=False,
            payloads=len(self.reproduction.payloads),
            wiki_files=len(self.reproduction.wiki),
            questions=0 if self.eval_set is None else len(self.eval_set.questions),
            episodes={name: envelope.episode_id for name, envelope in self.envelopes.items()},
        )

    @functools.cached_property
    def texts(self) -> tuple[FileText, ...]:
        """Everything a release would hand over, file by file, as text to check.

        Each file is read as it is written, line by line, and then every other way it reads
        (`pa_evals.decodings`), wherever that says something its lines don't. The wiki is
        read with its citations resolved and with its index, as it is released. The questions
        are read field by field, as the eval set is written afresh from them. A payload is
        released as it was left, so it is read in every decoding of its every part and
        header, and as ingest reads it.
        """
        left, where = self.reproduction, self.reproduction.where
        texts = [FileText(tuple(_field(f"{where}/{EXPECTATION}", left.expectation)))]
        if self.asked is not None:
            texts.append(FileText(tuple(_asked(f"{where}/{QUESTIONS}", self.asked))))
        for name, raw in left.payloads.items():
            decodings = [*self.decoded[name].decodings]
            if name in self.envelopes:
                decodings += _as_ingest_reads(self.envelopes[name])
            texts.append(
                _file_text(f"{where}/{PAYLOADS}/{name}", raw.decode(), decodings, payload=True)
            )
        wiki = dict(self.wiki) if self.index is None else {**self.wiki, INDEX: self.index}
        texts += [_file_text(f"{where}/{WIKI}/{path}", text) for path, text in wiki.items()]
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


def shape_problems(output: Path) -> list[Problem]:
    """What is wrong with the shape of the reproductions in a session's `output`.

    The directory of reproductions holds a directory for each, named for its finding. Each
    holds the files of the layout and nothing else: regular files of UTF-8 text, of the kinds
    their places take, within the caps. A link is never followed, and nothing is read that is
    over a cap."""
    top = output / REPRODUCTIONS
    if not top.exists(follow_symlinks=False):
        return []
    if top.is_symlink() or not top.is_dir():
        return [Problem(REPRODUCTIONS, "isn't a directory")]
    problems: list[Problem] = []
    for entry in sorted(top.iterdir()):
        where = f"{REPRODUCTIONS}/{entry.name}"
        if _is_directory(entry) and _FINDING.fullmatch(entry.name):
            problems += _shape(entry, where)
        else:
            problems.append(_misplaced(entry, where))
    return problems


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


def unmatched(output: Path, named: Collection[str]) -> list[Problem]:
    """Where the reproductions in a session's `output` aren't the ones its findings name:
    each finding in `named` with nothing in its reproduction's place (something there that
    isn't one is a problem of shape), and each reproduction that no finding names."""
    top = output / REPRODUCTIONS
    return [
        *(
            Problem(
                f"findings.json: {finding}.reproduction", f"there is no {REPRODUCTIONS}/{finding}"
            )
            for finding in named
            if not (top / finding).exists(follow_symlinks=False)
        ),
        *(
            Problem(f"{REPRODUCTIONS}/{finding}", "no finding names it as its reproduction")
            for finding in reproductions_left(output)
            if finding not in named
        ),
    ]


def problems_in_form(output: Path, named: Collection[str]) -> list[Problem]:
    """What keeps the reproductions in a session's `output` from being checked for what they
    say, beyond their shape (`shape_problems`), given the reproductions its findings name: one
    that isn't there or isn't named, and whatever trying each out finds (`Trial.problems`). None
    of it needs the case, so the session can be told and put it right."""
    problems = unmatched(output, named)
    for reproduction in read_reproductions(output):
        problems += try_out(reproduction).problems
    return problems


def read_reproductions(output: Path) -> list[Reproduction]:
    """Every reproduction in a session's `output`, by its finding's id. None of them is read
    unless the shape of all of them is right (`shape_problems`)."""
    if shape_problems(output):
        return []
    return [_read(output / REPRODUCTIONS / finding) for finding in reproductions_left(output)]


def _shape(directory: Path, where: str) -> list[Problem]:
    """What is wrong with the shape of the reproduction in `directory`."""
    problems: list[Problem] = []
    payloads: list[Path] = []
    wiki: list[Path] = []
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        here = f"{where}/{entry.name}"
        if entry.name in (EXPECTATION, QUESTIONS) and _is_file(entry):
            files.append(entry)
        elif entry.name == PAYLOADS and _is_directory(entry):
            problems += _payloads(entry, here, payloads)
        elif entry.name == WIKI and _is_directory(entry):
            problems += _wiki(entry, here, wiki)
        else:
            problems.append(_misplaced(entry, here))
    files += payloads + wiki
    size = sum(file.lstat().st_size for file in files)
    if len(payloads) > MOST_PAYLOADS:
        over = f"{len(payloads)} payloads, over the cap of {MOST_PAYLOADS}"
        problems.append(Problem(f"{where}/{PAYLOADS}", over))
    if len(wiki) > MOST_WIKI_FILES:
        problems.append(
            Problem(f"{where}/{WIKI}", f"{len(wiki)} files, over the cap of {MOST_WIKI_FILES}")
        )
    if size > MOST_BYTES:
        problems.append(Problem(where, f"{size} bytes, over the cap of {MOST_BYTES}"))
    else:
        problems += [
            Problem(f"{where}/{file.relative_to(directory).as_posix()}", "not UTF-8 text")
            for file in files
            if not is_text(file)
        ]
    problems += [
        Problem(f"{where}/{name}", "missing")
        for name in (EXPECTATION, QUESTIONS)
        if not (directory / name).exists(follow_symlinks=False)
    ]
    if not payloads and not (directory / PAYLOADS).is_symlink():
        problems.append(Problem(f"{where}/{PAYLOADS}", "missing: a reproduction has a payload"))
    return problems


def _payloads(directory: Path, where: str, found: list[Path]) -> Iterator[Problem]:
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


def _wiki(directory: Path, where: str, found: list[Path]) -> Iterator[Problem]:
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


def _misplaced(entry: Path, where: str) -> Problem:
    mode = entry.lstat().st_mode
    if stat.S_ISLNK(mode):
        return Problem(where, "a link in place of a file")
    if stat.S_ISDIR(mode):
        return Problem(where, "an unexpected directory")
    return Problem(where, "an unexpected file" if stat.S_ISREG(mode) else "not a regular file")


def _is_file(path: Path) -> bool:
    return stat.S_ISREG(path.lstat().st_mode)


def _is_directory(path: Path) -> bool:
    return stat.S_ISDIR(path.lstat().st_mode)


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


def try_out(reproduction: Reproduction) -> Trial:
    """`reproduction`, ingested into a throwaway data root as the fictional owner's, with its
    questions' evidence and its wiki's citations resolved to the episodes its payloads ingest
    to."""
    owner = FICTIONAL_OWNER
    form_problems = list(_expectation_problems(reproduction))
    asked, unread = _questions(reproduction)
    form_problems += unread
    as_of = datetime.now(UTC) if asked is None else asked.as_of
    envelopes = _envelopes(reproduction.payloads, owner, as_of)
    ids = {name: envelope.episode_id for name, envelope in envelopes.items()}
    wiki, uncited = _cited(reproduction, ids)
    form_problems += uncited
    index, lint_problems = None, []
    with tempfile.TemporaryDirectory(prefix="pa-reproduction-") as directory:
        data_root = DataRoot(Path(directory))
        for name, raw in reproduction.payloads.items():
            (data_root.inbox / name).parent.mkdir(parents=True, exist_ok=True)
            (data_root.inbox / name).write_bytes(raw)
        store = FilesystemL0(data_root.l0)
        with open_catalog(data_root.catalog, store) as catalog:
            summary = ingest(data_root.inbox, store, catalog, owner, now=lambda: as_of)
        refused = _quarantined(data_root.l0)
        ingest_problems = [
            *(
                Problem(f"{reproduction.where}/{PAYLOADS}/{name}", f"quarantined: {why}")
                for name, why in refused.items()
            ),
            *(Problem(f"{reproduction.where}/{PAYLOADS}", f"left: {why}") for why in summary.left),
            # A payload that is no episode is refused, whatever ingest made of it.
            *(
                Problem(f"{reproduction.where}/{PAYLOADS}/{name}", "can't be read as an episode")
                for name in reproduction.payloads
                if name not in envelopes and name not in refused
            ),
        ]
        ingests = not ingest_problems and ids.keys() == reproduction.payloads.keys()
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
            lint_problems = [
                Problem(
                    f"{reproduction.where}/{WIKI}/{error.path}"
                    + ("" if error.line is None else f":{error.line}"),
                    error.message,
                )
                for error in report.errors
            ]
    eval_set, owner_problems = None, []
    if asked is not None:
        unnamed = list(_evidence_problems(reproduction, asked))
        form_problems += unnamed
        if not unnamed and ingests:
            eval_set = _resolved(asked, ids)
        if asked.owner.to_owner() != owner:
            owner_problems.append(
                Problem(
                    f"{reproduction.where}/{QUESTIONS}",
                    "owner: isn't the synthetic corpus's fictional owner, whose every "
                    "reproduction is",
                )
            )
    return Trial(
        reproduction,
        envelopes,
        asked,
        eval_set,
        wiki,
        index,
        form_problems=tuple(form_problems),
        owner_problems=tuple(owner_problems),
        ingest_problems=tuple(ingest_problems),
        lint_problems=tuple(lint_problems),
        decoded={
            name: decode_payload(Source(PurePosixPath(name).parent.name), raw)
            for name, raw in reproduction.payloads.items()
        },
    )


def _envelopes(payloads: Mapping[str, bytes], owner: Owner, as_of: datetime) -> dict[str, Envelope]:
    """Each payload that can be an episode, as the envelope ingest makes of it for `owner`.
    A payload's episode id is its envelope's, whenever it is captured."""
    envelopes: dict[str, Envelope] = {}
    for name, raw in payloads.items():
        try:
            envelopes[name] = inbox_normalizer(PurePosixPath(name), owner)(raw, captured_at=as_of)
        except Exception:
            # Whatever a normalizer raises, the payload can't be an episode: that is ingest's
            # own rule (`pa_core.ingest`), and ingest says why when it quarantines it. A
            # payload left out here is refused by whoever tries the reproduction out.
            continue
    return envelopes


def _quarantined(l0: Path) -> dict[str, str]:
    """Each payload ingest quarantined into the store at `l0`, by its inbox name, with
    ingest's reason."""
    reasons: dict[str, str] = {}
    for path in sorted((l0 / "quarantine").glob("*.json")):
        if not path.with_suffix("").is_file():
            continue  # A quarantined payload that is itself JSON, not its record.
        record = QuarantineRecord.model_validate_json(path.read_bytes())
        reasons[record.inbox_name] = record.reason
    return reasons


def _expectation_problems(reproduction: Reproduction) -> Iterator[Problem]:
    where = f"{reproduction.where}/{EXPECTATION}"
    lines = [line for line in reproduction.expectation.splitlines() if line.strip()]
    if len(lines) != 1:
        yield Problem(where, f"{len(lines)} lines, where one says what should happen and what does")
    elif len(lines[0]) > EXPECTATION_CHARACTERS:
        yield Problem(
            where, f"{len(lines[0])} characters, over the cap of {EXPECTATION_CHARACTERS}"
        )


def _questions(reproduction: Reproduction) -> tuple[EvalSet | None, list[Problem]]:
    """The questions as they were left, their evidence still naming payloads; None, with why,
    when they aren't an eval set."""
    try:
        return EvalSet.model_validate_json(reproduction.questions), []
    except ValidationError as error:
        return None, [
            Problem(f"{reproduction.where}/{QUESTIONS}", what) for what in problems_of(error)
        ]


def _evidence_problems(reproduction: Reproduction, asked: EvalSet) -> Iterator[Problem]:
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
            yield Problem(
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


def _cited(
    reproduction: Reproduction, ids: Mapping[str, str]
) -> tuple[dict[str, str], list[Problem]]:
    """The starting wiki with each citation of a payload as a citation of the episode it
    ingests to, and each citation that names no payload of the reproduction."""
    problems: list[Problem] = []
    wiki: dict[str, str] = {}
    for path, text in reproduction.wiki.items():
        lines = text.splitlines(keepends=True)
        for number, line in enumerate(lines, start=1):
            for name in _CITED_PAYLOAD.findall(line):
                if name not in reproduction.payloads:
                    problems.append(
                        Problem(
                            f"{reproduction.where}/{WIKI}/{path}:{number}",
                            f"cites {name!r}, which isn't a payload of the reproduction",
                        )
                    )
        # A citation that can't be resolved is a problem already; what it names is no one's text.
        wiki[path] = _CITED_PAYLOAD.sub(
            lambda match: f"[ep:{ids[match[1]]}]" if match[1] in ids else "{ep:}", text
        )
    return wiki, problems


# What a reproduction says, as text to check.


def _file_text(
    file: str, written: str, decodings: Sequence[Decoding] = (), *, payload: bool = False
) -> FileText:
    """A file as text to check: its name, without its directory and its extension, each of
    its lines, and then every other way it reads that says something nothing before it does:
    each encoding any text may hold, read in the file as it is written and in each of
    `decodings`, which are in order, what is nearest the file's bytes first."""
    said = Said(written)
    kept: list[Decoding] = []
    for decoding in (Decoding("", written), *decodings):
        if decoding.what:
            if not said.new(decoding.text):
                continue
            kept.append(decoding)
        kept += [
            Decoding(", ".join(filter(None, (decoding.what, read.what))), read.text)
            for read in text_decodings(decoding.text)
            if said.new(read.text)
        ]
    return FileText(
        (
            Passage(f"{file}: its name", PurePosixPath(file).stem),
            *(
                Passage(f"{file}:{number}", line)
                for number, line in enumerate(written.splitlines(), start=1)
            ),
            *(Passage(f"{file}: {decoding.what}", decoding.text) for decoding in kept),
        ),
        payload=payload,
    )


def _asked(file: str, asked: EvalSet) -> Iterator[Passage]:
    """Every field of the questions that a session wrote freely, and each encoding any text
    may hold read in it."""
    fields = [
        ("owner.name", asked.owner.name),
        *(("owner.email_addresses", address) for address in asked.owner.email_addresses),
        *(("owner.other_names", name) for name in asked.owner.other_names),
        ("time_zone", asked.time_zone),
    ]
    for index, question in enumerate(asked.questions):
        fields += [
            (f"questions.{index}.id", question.id),
            (f"questions.{index}.question", question.question),
            (f"questions.{index}.expected_answer", question.expected_answer),
        ]
    for field, text in fields:
        yield from _field(f"{file}: {field}", text)


def _field(where: str, text: str) -> Iterator[Passage]:
    """A text that is read whole, and each encoding any text may hold read in it."""
    yield Passage(where, text)
    for decoding in text_decodings(text):
        yield Passage(f"{where}, {decoding.what}", decoding.text)


def _as_ingest_reads(envelope: Envelope) -> list[Decoding]:
    """What ingest reads out of a payload, which is what the librarian and the assistant are
    given of it."""
    read = [
        *(("a participant", person.name or "") for person in envelope.participants),
        *(("a participant's address", person.identifier) for person in envelope.participants),
        ("its subject", envelope.subject or ""),
        ("its body", envelope.body),
        ("its calendar's name", envelope.calendar_name or ""),
        *(("a label", label) for label in envelope.labels),
    ]
    return [Decoding(f"{what}, as ingest reads it", text) for what, text in read if text]


# After an edit: a released reproduction, resolved again.


class NotReleasedError(PaError):
    """A directory that isn't a released reproduction."""


class UnresolvedError(PaError):
    """A directory that can't be resolved again as a released reproduction. Its message says
    how much is wrong; `problems` says what and where, in the reproduction's own words."""

    def __init__(self, message: str, problems: tuple[Problem, ...] = ()) -> None:
        super().__init__(message)
        self.problems = problems


@dataclass(frozen=True)
class ResolvedAgain:
    """A released reproduction after `resolve_again`."""

    record: ReproductionRecord
    renewed: int
    """How many of its payloads ingest to another episode than its record said before."""


def resolve_again(directory: Path) -> ResolvedAgain:
    """Resolve the released reproduction in `directory` again, in place, after an edit.

    A development session renames or trims a released reproduction before it commits it, and
    an edited payload can ingest to another episode, whose id the questions and the wiki don't
    name. So the directory is put back in the form a session leaves, where a payload is named
    by its file, held to everything a session's is short of the case (its shape, and whatever
    trying it out finds), and written again as a release writes it.

    An episode id is the payload's that ingests to it now, or else the payload's that the
    record says it was. Evidence and citations that name a payload by its file, as a session
    writes them, are resolved too. Raises UnresolvedError, and leaves the directory as it was,
    when anything is wrong with it, and NotReleasedError when it has no record.
    """
    was = _record(directory)
    with tempfile.TemporaryDirectory(prefix="pa-reproduction-") as scratch:
        output = Path(scratch)
        problems = _as_left(directory, output / REPRODUCTIONS / was.finding, was)
        problems += shape_problems(output)
        trial = None
        if not problems:
            [reproduction] = read_reproductions(output)
            trial = try_out(reproduction)
            problems += trial.problems
    if trial is None or problems:
        raise UnresolvedError(
            f"{directory} isn't resolved again, and is left as it was "
            f"({len(problems)} problem{'' if len(problems) == 1 else 's'})",
            tuple(
                Problem(_as_released(problem.where, was.finding), problem.what)
                for problem in problems
            ),
        )
    trial.write(directory)
    if trial.index is None:
        (directory / WIKI / INDEX).unlink(missing_ok=True)
    record = trial.record
    renewed = sum(was.episodes.get(name) != episode for name, episode in record.episodes.items())
    return ResolvedAgain(record, renewed)


def _record(directory: Path) -> ReproductionRecord:
    """The record of the released reproduction in `directory`."""
    try:
        record = ReproductionRecord.model_validate_json((directory / RECORD).read_bytes())
    except (OSError, ValidationError):
        record = None
    if record is None or not _FINDING.fullmatch(record.finding):
        raise NotReleasedError(
            f"{directory} isn't a released reproduction: it has no {RECORD} that says which "
            "finding it is for and which episode each payload was"
        )
    return record


_NO_PAYLOAD = (
    "an episode that none of the payloads is: its payload was removed, or renamed and edited "
    "at once"
)


def _as_released(where: str, finding: str) -> str:
    """A place in a reproduction as a session leaves it, from the session's output, as the
    place it is in the released one, from its own directory."""
    place = where.removeprefix(f"{REPRODUCTIONS}/{finding}").removeprefix("/") or "."
    return EVAL_SET + place.removeprefix(QUESTIONS) if place.startswith(QUESTIONS) else place


def _as_left(released: Path, left: Path, was: ReproductionRecord) -> list[Problem]:
    """Write the released reproduction in `released` to `left` in the form a session leaves:
    each episode id as the file of the payload it is, and no index or record. Returns each
    place that names an episode none of the payloads is, from the session's output.

    Nothing is read or written through a link, and whatever isn't the text its place takes is
    copied as it is, for the shape to refuse."""

    def written_by_a_release(directory: str, entries: list[str]) -> set[str]:
        if Path(directory) == released:
            return {EVAL_SET, RECORD}.intersection(entries)
        return {INDEX}.intersection(entries) if Path(directory) == released / WIKI else set()

    shutil.copytree(released, left, symlinks=True, ignore=written_by_a_release)
    names = _payloads_by_episode(left / PAYLOADS, was)
    where = f"{REPRODUCTIONS}/{was.finding}"
    problems: list[Problem] = []

    def by_file(field: str, evidence: list[str]) -> list[str]:
        """`evidence` with each episode id as its payload's file. What names a payload by its
        file already stands."""
        if any(named not in names and "/" not in named for named in evidence):
            problems.append(Problem(f"{where}/{QUESTIONS}", f"{field}: names {_NO_PAYLOAD}"))
        return [names.get(named, named) for named in evidence]

    questions = released / EVAL_SET
    if questions.exists(follow_symlinks=False) and _is_file(questions):
        asked = questions.read_bytes()
        try:
            eval_set = EvalSet.model_validate_json(asked)
        except ValidationError:
            pass  # Trying it out says what is wrong with them.
        else:
            evidence = [
                by_file(f"questions.{index}.evidence", question.evidence)
                for index, question in enumerate(eval_set.questions)
            ]
            by_payload = eval_set.model_copy(
                update={
                    "questions": [
                        question.model_copy(update={"evidence": named})
                        for question, named in zip(eval_set.questions, evidence, strict=True)
                    ],
                    "adversarial": by_file("adversarial", eval_set.adversarial),
                }
            )
            asked = by_payload.model_dump_json().encode()
        (left / QUESTIONS).write_bytes(asked)
    for page in regular_files(left / WIKI):
        if page.suffix != ".md" or not is_text(page):
            continue
        lines = page.read_text(encoding="utf-8").splitlines(keepends=True)
        for number, line in enumerate(lines, start=1):
            if any(cited not in names for cited in CITATION.findall(line)):
                place = f"{where}/{page.relative_to(left).as_posix()}:{number}"
                problems.append(Problem(place, f"cites {_NO_PAYLOAD}"))
        page.write_text(
            CITATION.sub(
                lambda cited: f"{{ep:{names[cited[1]]}}}" if cited[1] in names else cited[0],
                "".join(lines),
            ),
            encoding="utf-8",
        )
    return problems


def _payloads_by_episode(payloads: Path, was: ReproductionRecord) -> dict[str, str]:
    """The file of the payload each episode id is, among those in `payloads`: the payload
    that ingests to it now, or else the one the record says ingested to it, which was edited
    since."""
    now = {
        path.relative_to(payloads).as_posix(): path.read_bytes() for path in regular_files(payloads)
    }
    return {
        **{episode: name for name, episode in was.episodes.items() if name in now},
        **{
            envelope.episode_id: name
            for name, envelope in _envelopes(now, FICTIONAL_OWNER, datetime.now(UTC)).items()
        },
    }


# As it is run: a released reproduction's parts, for whatever asks or dreams it.


@dataclass(frozen=True)
class Released:
    """A released reproduction, as a run over it or a dreaming run of its wiki takes it."""

    finding: str
    """The finding it is for, by id: what a report names it by, since its directory is
    wherever it was copied to."""
    payloads: Path
    """Its raw payloads, laid out as a corpus is: a directory per source."""
    eval_set: Path
    """Its questions, their evidence named by episode id."""
    wiki: Path | None
    """Its starting wiki's tree, which the diagnostician wrote and no librarian built; None
    when its fault needs none."""


def read_released(directory: Path) -> Released:
    """The released reproduction in `directory`: a release's, or a development session's copy
    of one. Raises NotReleasedError for a directory that isn't one."""
    record = _record(directory)
    payloads, eval_set, wiki = directory / PAYLOADS, directory / EVAL_SET, directory / WIKI
    missing = [
        name
        for name, there in ((f"{PAYLOADS}/", payloads.is_dir()), (EVAL_SET, eval_set.is_file()))
        if not there
    ]
    if missing:
        raise NotReleasedError(
            f"{directory} isn't a released reproduction: it has no {' and no '.join(missing)}"
        )
    return Released(record.finding, payloads, eval_set, wiki if wiki.is_dir() else None)
