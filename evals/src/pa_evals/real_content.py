"""Real content: what a case holds that a development session must never read, and how to
find it in text (ADR-0010).

The release check (`pa_evals.release_check`) owns the rules. This module gives it what they
are made of:

- **what a case holds**: the names of its people and organizations (`Identifier`), and its
  text (`Passage`), gathered from the case's parts. Each part is a `Source`: `Envelopes`,
  `Wiki`, `History`, `OwnerIdentity`, and `Text` for a part that is text alone (an eval set,
  runs, an earlier analysis). A case that gains a part gains a `Source` for it, and nothing
  else here changes;
- **what may be repeated**: the text of the repository (`repository_passages`), which is
  public already;
- **how each kind of real content is found** in a text: `name_runs` and `named` for names,
  `unreserved` and `holding_dotted_names` for addresses and domains, `repeated` for runs of
  words, and `long_numbers` for long numbers.

Text is compared as words (`words`): letters and digits in lower case, without accents and
without the characters that don't show, so a name or a sentence is found whatever it is
written with or between. What can't be found this way is anything reworded, respelled, or
encoded: the check catches identifiers and quoted text, never meaning.

Everything here that is found or gathered is real content. None of it goes in an exception.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from pa_core.envelope import Envelope
from pa_core.errors import PaError
from pa_core.librarian.wiki import (
    LINK,
    ORGANIZATION,
    PERSON,
    RETIRED,
    TOPIC,
    PageFormatError,
    parse_retirement,
    read_frontmatter,
)
from pa_core.owner import Owner

NAME_WORDS = 2
"""The fewest words a name has to be refused wherever it is written. A name of one word is
mostly an ordinary word ("will", "may", "mark")."""

QUOTED_WORDS = 6
"""How many words in a row, found in the case, make a quote of it."""
LONG_NUMBER_DIGITS = 7
"""How many digits make a number long enough to be a phone, an account, or a confirmation
number."""
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
"""The domains reserved for examples (RFC 2606), which pass with every name under them."""
RESERVED_TOP_LEVEL_DOMAINS = ("example", "invalid", "test")
"""The top-level domains reserved for examples, which pass with every name under them."""

Words = tuple[str, ...]
"""Words in a row, as `words` reads them."""


class RealContentError(PaError):
    """A case whose real content can't be gathered, so nothing can be checked against it. Its
    message never quotes the case."""


# Text as words.


@functools.cache
def _unseen() -> dict[int, None]:
    """Every character that doesn't show by itself: the marks that sit on a letter, the format
    characters (a zero-width space, a soft hyphen, a direction mark), and the control
    characters that aren't white space."""
    return {
        point: None
        for point in range(sys.maxunicode + 1)
        if unicodedata.category(chr(point)) in ("Mn", "Me", "Cf")
        or (unicodedata.category(chr(point)) == "Cc" and not chr(point).isspace())
    }


_ASCII_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")


def plain(text: str) -> str:
    """`text` as it reads: compatibility forms as the characters they stand for (a full-width
    letter or digit as the plain one), with no accents and nothing that doesn't show."""
    if text.isascii():
        return _ASCII_CONTROL.sub("", text)
    return unicodedata.normalize("NFKD", text).translate(_unseen())


_WORD = re.compile(r"[^\W_]+")


def words(text: str) -> list[str]:
    """The words of `text`, in order: each run of letters and digits, in lower case."""
    return _WORD.findall(plain(text).casefold())


# What a case holds.


@dataclass(frozen=True)
class Identifier:
    """A name the case holds for a person or an organization, as it is written there."""

    text: str
    held: str
    """What holds it, for the check's private notes: "a participant's display name"."""


@dataclass(frozen=True)
class Passage:
    """A piece of text: an episode's body, a wiki file, a file in the repository."""

    where: str
    """What it is, for the check's private notes."""
    text: str


class Source(Protocol):
    """One part of a case, as the release check reads it."""

    def identifiers(self) -> Iterable[Identifier]:
        """The names it holds for people and organizations."""
        ...

    def passages(self) -> Iterable[Passage]:
        """Its text, which findings may not repeat."""
        ...


@dataclass(frozen=True)
class Envelopes:
    """The episodes of an L0 store: each participant's display name, and each episode's
    subject and body. The envelopes are read as files, never through the store, which may
    write when it is opened."""

    l0: Path

    def identifiers(self) -> Iterator[Identifier]:
        for envelope in self._envelopes():
            for participant in envelope.participants:
                if participant.name:
                    yield Identifier(participant.name, "a participant's display name")

    def passages(self) -> Iterator[Passage]:
        for envelope in self._envelopes():
            if envelope.subject:
                yield Passage(f"{envelope.episode_id}'s subject", envelope.subject)
            yield Passage(f"{envelope.episode_id}'s body", envelope.body)

    def _envelopes(self) -> Iterator[Envelope]:
        for path in sorted((self.l0 / "episodes").glob("*/*/*.json")):
            try:
                if path.is_symlink():
                    raise OSError("a link")
                yield Envelope.model_validate_json(path.read_bytes())
            except (OSError, ValidationError):
                raise RealContentError(
                    f"an envelope in {self.l0} can't be read, so what the case holds can't be "
                    "gathered and nothing can be checked against it"
                ) from None


_NAMED_FOLDERS = (PERSON.folder, ORGANIZATION.folder)
"""The wiki folders whose pages are people and organizations."""
_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)
_ALIASES = re.compile(r"^aliases:(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class Wiki:
    """A wiki's tree: the names it holds for people and organizations, and every file's text.

    The names are each person's and organization's page's slug, aliases, and name. They are
    also every slug that has no page, with what a link to it shows: one that is linked, since
    a wiki links someone long before it gives them a page, and one that dreaming retired.
    Nothing says such a slug isn't a person's. A topic's slug and aliases are no one's name.
    They are subject matter, which the diagnostician's instructions govern, not the check.

    A page is read for its names whether or not it follows the schema, and nothing is read
    through a link in the file system.
    """

    tree: Path
    name: str = "wiki"
    """What the check's notes call this wiki."""

    def identifiers(self) -> Iterator[Identifier]:
        files = list(self._files())
        topics = {path.stem for path, _ in files if path.parts[0] == TOPIC.folder}
        for path, text in files:
            if path.parts[0] not in _NAMED_FOLDERS or len(path.parts) == 1:
                continue
            page = f"{self.name}/{path.as_posix()}"
            yield Identifier(path.stem, f"the slug of {page}")
            title = _TITLE.search(text)
            if title:
                yield Identifier(title[1], f"the name of {page}")
            for alias in _aliases(text):
                yield Identifier(alias, f"an alias of {page}")
        for path, text in files:
            file = f"{self.name}/{path.as_posix()}"
            if path.as_posix() == RETIRED:
                for retirement in filter(None, map(parse_retirement, text.splitlines())):
                    yield Identifier(retirement.slug, f"a slug retired in {file}")
            for link in LINK.finditer(text):
                if link["slug"] in topics:
                    continue
                yield Identifier(link["slug"], f"a slug linked in {file}")
                if link["shown"]:
                    yield Identifier(link["shown"], f"what a link shows in {file}")

    def passages(self) -> Iterator[Passage]:
        for path, text in self._files():
            yield Passage(f"{self.name}/{path.as_posix()}", text)

    def _files(self) -> Iterator[tuple[Path, str]]:
        """Each regular file in the tree, by its path from the tree's root, with its text."""
        if self.tree.is_symlink():
            return
        for directory, _, names in self.tree.walk():
            for name in sorted(names):
                path = directory / name
                if path.is_symlink() or not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                yield path.relative_to(self.tree), text


def _aliases(page: str) -> Sequence[str]:
    """The aliases in a page's frontmatter; read loosely when the frontmatter can't be read
    as the schema writes it."""
    try:
        aliases = read_frontmatter(page.splitlines())[0].get("aliases", ())
        return aliases if isinstance(aliases, tuple) else [aliases]
    except PageFormatError:
        return [
            alias.strip(" \"'")
            for line in _ALIASES.findall(page)
            for alias in line.strip(" []").split(",")
        ]


_PAGE_IN_A_DIFF = re.compile(r"^diff --git a/(?P<path>\S+) b/", re.MULTILINE)
_TITLE_IN_A_DIFF = re.compile(r"^[-+ ]# (.+)$", re.MULTILINE)
_ALIASES_IN_A_DIFF = re.compile(r"^[-+ ]aliases:(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class History:
    """A wiki's history, exported to files (`pa_evals.wiki_history`): the text of every run,
    which holds lines no wiki holds any more, and the names of the people's and organizations'
    pages a run changed: each one's slug, and the name and aliases its diff shows. A page a run
    deleted is named nowhere else."""

    files: Sequence[Path]
    name: str = "wiki"
    """What the check's notes call the wiki whose history this is."""

    def identifiers(self) -> Iterator[Identifier]:
        held = f"in {self.name}'s history"
        for _, text in self._runs():
            pages = list(_PAGE_IN_A_DIFF.finditer(text))
            # A run that changed no page (the wiki's first commit) has none to pair.
            for page, following in zip(pages, [*pages[1:], None], strict=False):
                path = Path(page["path"])
                if path.parts[0] not in _NAMED_FOLDERS or len(path.parts) == 1:
                    continue
                diff = text[page.end() : following.start() if following else len(text)]
                yield Identifier(path.stem, f"the slug of a page {held}")
                for title in _TITLE_IN_A_DIFF.findall(diff):
                    yield Identifier(title, f"the name of a page {held}")
                for line in _ALIASES_IN_A_DIFF.findall(diff):
                    for alias in line.strip(" []").split(","):
                        yield Identifier(alias.strip(" \"'"), f"an alias of a page {held}")

    def passages(self) -> Iterator[Passage]:
        for file, text in self._runs():
            yield Passage(f"{self.name}'s history, {file.name}", text)

    def _runs(self) -> Iterator[tuple[Path, str]]:
        for file in self.files:
            yield file, file.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class Text:
    """Text a case holds that names no one by itself, such as an eval set's questions, a run's
    answers, and an earlier private analysis: `read` gives each piece when it is asked."""

    read: Callable[[], Iterable[Passage]]

    def identifiers(self) -> Iterator[Identifier]:
        return iter(())

    def passages(self) -> Iterator[Passage]:
        yield from self.read()


@dataclass(frozen=True)
class OwnerIdentity:
    """The owner's own names. Their addresses fall to the rule for addresses."""

    owner: Owner

    def identifiers(self) -> Iterator[Identifier]:
        yield Identifier(self.owner.name, "the owner's name")
        for name in self.owner.other_names:
            yield Identifier(name, "one of the owner's other names")

    def passages(self) -> Iterator[Passage]:
        return iter(())


@dataclass(frozen=True)
class RealContent:
    """What a case holds, gathered from its parts."""

    sources: Sequence[Source]

    @functools.cached_property
    def names(self) -> Mapping[Words, str]:
        """Every name the case holds, as the runs of words that are refused (`name_runs`),
        each with what holds it."""
        names: dict[Words, str] = {}
        for source in self.sources:
            for identifier in source.identifiers():
                for run in name_runs(identifier.text):
                    names.setdefault(run, identifier.held)
        return names

    def passages(self) -> Iterator[Passage]:
        for source in self.sources:
            yield from source.passages()


# Names.

_LINKING_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "do", "for", "from", "in", "no", "not", "of", "on"}
    | {"or", "re", "the", "to", "via", "with"}
)
"""Words a part of a name can't start or end with. A display name or an alias that holds one
("Reply via the Portal", "the water utility") would otherwise refuse ordinary prose."""


def name_runs(name: str) -> set[Words]:
    """The runs of words that `name` refuses, each of `NAME_WORDS` words or more.

    - The whole name, as it is written. A slug's words count: `jordan-reyes` is "jordan
      reyes".
    - The name turned round, when it is written family name first ("Reyes, Jordan").
    - The name without its initials ("Jordan A. Reyes").
    - Every part of it that is a name by itself: words in a row that neither start nor end
      with a linking word or an initial, so "Dr. Jordan Reyes (Acme)" refuses "Jordan Reyes".
    """
    orders = [words(name)]
    family, comma, given = name.partition(",")
    if comma and "," not in given:
        orders.append(words(given) + words(family))
    runs: set[Words] = set()
    for order in orders:
        for whole in (order, [word for word in order if len(word) > 1]):
            if len(whole) >= NAME_WORDS:
                runs.add(tuple(whole))
            runs.update(
                tuple(whole[start:end])
                for start in range(len(whole))
                for end in range(start + NAME_WORDS, len(whole) + 1)
                if _ends_a_name(whole[start]) and _ends_a_name(whole[end - 1])
            )
    return runs


def _ends_a_name(word: str) -> bool:
    return len(word) > 1 and word not in _LINKING_WORDS


def named(text: Sequence[str], names: Mapping[Words, str]) -> list[tuple[int, int]]:
    """Where `text`, as words, holds one of `names`: each place as the indexes its words run
    from and to. A name is found as its words in a row, or as one word that is its words run
    together ("jordanreyes"). The longest name at a place counts, and a name inside it
    doesn't."""
    longest = max(map(len, names), default=0)
    together = {"".join(name) for name in names}
    places: list[tuple[int, int]] = []
    for start in range(len(text)):
        end = next(
            (
                start + length
                for length in range(min(longest, len(text) - start), NAME_WORDS - 1, -1)
                if tuple(text[start : start + length]) in names
            ),
            start + 1 if text[start] in together else None,
        )
        if end is not None and (not places or end > places[-1][1]):
            places.append((start, end))
    return places


def held_by(name: Words, names: Mapping[Words, str]) -> str:
    """What in the case holds `name`, which `named` found: its words in a row, or run
    together."""
    if name in names:
        return names[name]
    return next(held for run, held in names.items() if "".join(run) == name[0])


# Addresses and domains.

_LABEL = r"[\w-]+"
_ADDRESS = re.compile(rf"[^\s@]+@(?P<domain>{_LABEL}(?:\.{_LABEL})*)")
_DOTTED = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_TOP_LEVEL = re.compile(r"[^\W\d_][\w-]+")
"""What a domain can end with: two characters or more, starting with a letter. So an
abbreviation (`e.g.`), a figure (`2.5`), and a version (`v0.14.1`) aren't domains."""


@dataclass(frozen=True)
class Unreserved:
    """What a text holds that is outside the reserved example domains."""

    addresses: Sequence[str]
    """Each address, which is refused whatever else holds its domain."""
    dotted_names: Sequence[str]
    """Each dotted name that could be a domain, in lower case. One is a domain unless it is
    code or a file's name, which only the repository can say (`holding_dotted_names`)."""


def unreserved(text: str) -> Unreserved:
    """The addresses and the dotted names in `text` that are outside the reserved example
    domains. An address is anything written around an `@`, whatever its host."""
    text = plain(text).replace("\u3002", ".")  # An ideographic full stop reads as a dot.
    addresses = [match[0] for match in _ADDRESS.finditer(text) if not _is_reserved(match["domain"])]
    dotted = [_domain(match[0]) for match in _DOTTED.finditer(_ADDRESS.sub(" ", text))]
    return Unreserved(addresses, [name for name in dotted if name and not _is_reserved(name)])


def _domain(dotted: str) -> str | None:
    """The domain a dotted name could be, in lower case: the name up to its last part that a
    domain can end with. None when that leaves no dotted name."""
    labels = dotted.casefold().split(".")
    while labels and not _TOP_LEVEL.fullmatch(labels[-1]):
        labels.pop()
    return ".".join(labels) if len(labels) > 1 else None


def _is_reserved(domain: str) -> bool:
    """Whether `domain` is a reserved example domain or a name under one. Never by how it
    starts or what it holds: `example.com.elsewhere.net` and `notexample.com` aren't."""
    labels = domain.casefold().split(".")
    return labels[-1] in RESERVED_TOP_LEVEL_DOMAINS or ".".join(labels[-2:]) in RESERVED_DOMAINS


def holding_dotted_names(passages: Iterable[Passage], names: Collection[str]) -> set[str]:
    """Which of the dotted `names` (in lower case) the `passages` hold, in any letter case:
    whole, or as the end or the middle of a longer dotted name (`cut.episode_ids` in
    `case.cut.episode_ids()`), never as part of a word."""
    wanted = {name: re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])") for name in names}
    held: set[str] = set()
    for passage in passages:
        if len(held) == len(wanted):
            break
        text = passage.text.casefold()
        held.update(
            name
            for name, pattern in wanted.items()
            if name not in held and name in text and pattern.search(text)
        )
    return held


# Quoted text.


def repeated(passages: Iterable[Passage], runs: Collection[Words]) -> dict[Words, str]:
    """Which of `runs`, each of `QUOTED_WORDS` words, the `passages` hold, each with the first
    passage that holds it. A run is held wherever its words follow one another in a passage,
    whatever is written between them. The passages are read one at a time and never kept, so
    a case of any size can be read."""
    held: dict[Words, str] = {}
    if not runs:
        return held
    starts = {run[0] for run in runs}
    for passage in passages:
        if len(held) == len(runs):
            break
        text = words(passage.text)
        for index in range(len(text) - QUOTED_WORDS + 1):
            if text[index] in starts:
                run = tuple(text[index : index + QUOTED_WORDS])
                if run in runs:
                    held.setdefault(run, passage.where)
    return held


# Long numbers.

_ID = re.compile(r"(?<![0-9A-Za-z_])[a-z][a-z_]*_[0-9a-f]{16}(?![0-9A-Za-z])")
"""Something of an episode id's shape: a source, then 16 hex characters."""
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_NUMBER = re.compile(r"\d(?:[()]?[ \t.\u2010-\u2015-]?[()]?\d)*")
"""Digits written as one number: in a row, or in groups with a space, a hyphen, a dot, or a
bracket between them, as a phone or a card number is."""


def long_numbers(text: str, *, ids: Collection[str] = ()) -> list[str]:
    """Each number in `text` of `LONG_NUMBER_DIGITS` digits or more, as it is written: digits
    in a row, or in the groups a phone or a card number is written in.

    A date written `2026-10-05` isn't a number. Neither is one of `ids`, the episode ids that
    may be named, though 16 hex characters can hold as many digits.
    """
    text = _ID.sub(lambda match: "id" if match[0] in ids else match[0], plain(text))
    text = _DATE.sub(lambda match: "date" if _is_date(match[0]) else match[0], text)
    return [
        match[0]
        for match in _NUMBER.finditer(text)
        if sum(character.isdigit() for character in match[0]) >= LONG_NUMBER_DIGITS
    ]


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


# The repository, which is public already.


def tracked_files(repository: Path) -> list[str]:
    """Every file git tracks in `repository`, as paths from its root."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raise RealContentError(
            f"can't list the files git tracks in {repository}: the release check runs in a "
            "checkout of the repository"
        ) from None
    return [name for name in listed.split("\0") if name]


def repository_passages(repository: Path) -> Iterator[Passage]:
    """The text of every file git tracks in `repository`, and the files' own names."""
    names = tracked_files(repository)
    yield Passage("the repository's file names", "\n".join(names))
    for name in names:
        path = repository / name
        if path.is_symlink() or not path.is_file():
            continue
        yield Passage(name, path.read_text(encoding="utf-8", errors="replace"))
