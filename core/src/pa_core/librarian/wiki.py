"""The wiki model: the wiki's layout, slugs, citations, and line formats (spec #56).

The wiki is `index.md`, four folders of Markdown, `days/` (day logs, `YYYY-MM-DD.md`) and
`people/`, `orgs/` and `topics/` (pages, `<slug>.md`), and the root files: `proposals.md`, what
the librarian asks the owner (spec #140). Nothing else may exist in it.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

TREE = "wiki"
"""The wiki's working tree, by this name in a data root and in a replay's target directory."""
GIT_DIR = "wiki.git"
"""The wiki's git directory, beside its tree and never inside it."""

INDEX = "index.md"
"""The generated index, at the wiki's top level."""
PROPOSALS = "proposals.md"
"""The proposals: what the librarian asks the owner, open and answered."""
ROOT_FILES = (PROPOSALS,)
"""The files at the wiki's top level beside the index, each with a line format of its own."""
DAYS = "days"
"""The folder of day logs."""

CURRENT_STATE, DECISIONS, OPEN_LOOPS, NOTES = "Current state", "Decisions", "Open loops", "Notes"
"""The sections a page can have (`## <name>`). Current state and Notes are free-form."""
FIELDS = ("type", "aliases", "status", "summary", "updated")
"""The frontmatter every page has."""
ACTIVE = "active"
"""The `status` of a page that's live, whatever its type."""


@dataclass(frozen=True)
class PageType:
    """A type of page, as the wiki schema gives it."""

    folder: str
    """The folder its pages are in."""
    type: str
    """Its name, which its pages' `type` must be."""
    heading: str
    """The heading its pages are listed under in `index.md`."""
    sections: tuple[str, ...]
    """Its sections, in order."""
    statuses: tuple[str, ...]
    """Its allowed `status` values."""
    list_field: str | None = None
    """The list of identifiers it adds to its frontmatter, if any: a person's email addresses,
    an organization's mail domains."""
    identifier: str | None = None
    """What one of those identifiers is, with its article: "an address", "a domain"."""

    @property
    def fields(self) -> tuple[str, ...]:
        """Its frontmatter: every page's, then its list of identifiers."""
        return (*FIELDS, *((self.list_field,) if self.list_field else ()))


PERSON = PageType(
    folder="people",
    type="person",
    heading="People",
    sections=(CURRENT_STATE, OPEN_LOOPS, NOTES),
    statuses=("active", "inactive"),
    list_field="emails",
    identifier="an address",
)
ORGANIZATION = PageType(
    folder="orgs",
    type="organization",
    heading="Organizations",
    sections=(CURRENT_STATE, OPEN_LOOPS, NOTES),
    statuses=("active", "inactive"),
    list_field="domains",
    identifier="a domain",
)
TOPIC = PageType(
    folder="topics",
    type="topic",
    heading="Topics",
    sections=(CURRENT_STATE, DECISIONS, OPEN_LOOPS, NOTES),
    statuses=("active", "settled", "dormant"),
)
PAGE_TYPES = {page_type.folder: page_type for page_type in (PERSON, ORGANIZATION, TOPIC)}
"""Each page folder's page type, in the order `index.md` lists them."""
PAGE_FOLDERS = tuple(PAGE_TYPES)
"""The folders of pages."""
FOLDERS = (DAYS, *PAGE_FOLDERS)

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""Lowercase ASCII words joined by hyphens: a page's name, and how it's linked."""
OWNER = "me"
"""How the wiki writes the owner, who is never linked and never gets a page."""
DAY_LOG_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
PAGE_NAME = re.compile(rf"{SLUG.pattern}\.md")


def is_page_name(name: str) -> bool:
    """Whether `name` is a page's file name, `<slug>.md`; never a `CLAUDE.md`, in any case."""
    return PAGE_NAME.fullmatch(name) is not None and name.lower() != "claude.md"


def day_of(name: str) -> date | None:
    """The day a day log's file name, `YYYY-MM-DD.md`, is for; None if it names no real day."""
    if not DAY_LOG_NAME.fullmatch(name):
        return None
    try:
        return date.fromisoformat(name.removesuffix(".md"))
    except ValueError:
        return None


def folder_files(tree: Path, folder: str) -> list[Path]:
    """The regular files directly in `folder` of the wiki at `tree`, by name, never following a
    symbolic link; none when the folder isn't a directory. Lint reports anything else."""
    directory = tree / folder
    if not directory.is_dir() or directory.is_symlink():
        return []
    return [
        entry for entry in sorted(directory.iterdir()) if entry.is_file() and not entry.is_symlink()
    ]


# Deliberately stricter than evals' citations.py, which forgives spaces in what an answer cites;
# lint holds the wiki to the exact form.
CITATION = re.compile(r"\[ep:([A-Za-z0-9_-]+)\]")
"""A citation of one episode, `[ep:<episode_id>]`: plain text, never a wikilink."""
_SHOWN = r"[^\[\]|\n]+"
LINK = re.compile(rf"\[\[(?P<slug>{SLUG.pattern})(?:\|(?P<shown>{_SHOWN}))?\]\]")
"""A link to a person, organization, or topic by slug, whether or not it has a page yet:
`[[slug]]`, or `[[slug|text]]` showing `text` in its place (as a link rewritten from an alias
keeps the name it was written with)."""
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
"""A template's `<placeholder>`, which a page or day log written from it replaces."""
NEEDS_LOOKUP = re.compile(r"\[needs lookup: (?P<question>[^\]]+)\]")
"""Outside information the librarian couldn't find in L0, marked inline where it's missing."""

_OBSERVATION = re.compile(
    rf"- (?P<text>\S.*?) (?P<citations>{CITATION.pattern}(?: {CITATION.pattern})*)"
)


@dataclass(frozen=True)
class Observation:
    """One day-log line: what one episode shows, linking at least one slug."""

    text: str
    links: tuple[str, ...]
    citations: tuple[str, ...]


def links(text: str) -> list[str]:
    """The slugs `text` links, in order, piped links included."""
    return [match["slug"] for match in LINK.finditer(text)]


def parse_observation(line: str) -> Observation | None:
    """The observation `line` holds, or None if it isn't one.

    The format is `- <one line, linking at least one [[slug]]> [ep:<id>]`, with one or more
    citations at the end.
    """
    match = _OBSERVATION.fullmatch(line)
    if match is None:
        return None
    linked = tuple(links(match["text"]))
    if not linked:
        return None
    return Observation(match["text"], linked, tuple(CITATION.findall(match["citations"])))


def slugify(text: str) -> str:
    """The slug for a name: lowercase ASCII words joined by hyphens ("Zoë Brandt" is
    `zoe-brandt`); empty when the name has no letters or digits."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_text.lower()))


def citations(text: str) -> list[str]:
    """The episode ids `text` cites, in order."""
    return CITATION.findall(text)


# Decision and loop lines. A party is `me` or a link; `<who>` is parties joined by ", ".

_DATE = r"\d{4}-\d{2}-\d{2}"
_CITES = r"\[ep:[A-Za-z0-9_-]+\](?: \[ep:[A-Za-z0-9_-]+\])*"
_PARTY = rf"(?:{OWNER}|\[\[{SLUG.pattern}(?:\|{_SHOWN})?\]\])"
_DECIDED = (
    rf"(?P<day>{_DATE}) · (?P<subject>[^:]+?): (?P<stance>proposed|decided|rejected) "
    rf"(?P<choice>\S.*?) \((?P<who>{_PARTY}(?:, {_PARTY})*)\) (?P<citations>{_CITES})"
)
_DECISION = re.compile(rf"- {_DECIDED}")
_SUPERSEDED_DECISION = re.compile(
    rf"- ~~{_DECIDED}~~ superseded (?P<by_day>{_DATE}) by (?P<by>\S.*?) (?P<by_citations>{_CITES})"
)
_LOOP = re.compile(
    rf"- \[(?P<mark>[ x])\] (?P<by>{_PARTY}) → (?P<to>{_PARTY}): (?P<what>\S.*?) · "
    rf"(?P<kind>asked|promised) (?P<opened>{_DATE})(?: · due (?P<due>{_DATE}))? "
    rf"(?P<citations>{_CITES})(?: · closed (?P<closed>{_DATE}) (?P<closing>{_CITES}))?"
)

DECISION_FORMAT = (
    "- YYYY-MM-DD · <subject>: <proposed|decided|rejected> <choice> (<who>) [ep:<id>], "
    "struck through when superseded: - ~~<the decision, with its citation>~~ superseded "
    "YYYY-MM-DD by <choice> [ep:<id>]"
)
LOOP_FORMAT = (
    "- [ ] <owed by> → <owed to>: <what> · <asked|promised> YYYY-MM-DD[ · due YYYY-MM-DD] "
    "[ep:<id>], closed as - [x] <the same> · closed YYYY-MM-DD [ep:<id>]"
)


@dataclass(frozen=True)
class Supersession:
    """The later decision that replaced a decision line, which stays struck through."""

    day: date
    choice: str
    citations: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    """A dated decision line on a topic page: who proposed, decided, or rejected what."""

    day: date
    subject: str
    stance: str
    """proposed, decided, or rejected."""
    choice: str
    who: tuple[str, ...]
    """The parties: `me`, or slugs."""
    citations: tuple[str, ...]
    superseded: Supersession | None = None


@dataclass(frozen=True)
class Closure:
    """When a loop was closed, and the episodes that settled it."""

    day: date
    citations: tuple[str, ...]


@dataclass(frozen=True)
class Loop:
    """An open loop line: something owed by one party to another, asked or promised."""

    owed_by: str
    """`me`, or a slug."""
    owed_to: str
    what: str
    kind: str
    """asked (the party owed asked) or promised (the party owing promised)."""
    opened: date
    due: date | None
    citations: tuple[str, ...]
    """The episodes that opened it."""
    closed: Closure | None = None

    @property
    def is_open(self) -> bool:
        return self.closed is None


def parse_decision(line: str) -> Decision | None:
    """The decision `line` holds, or None if it isn't one in the schema's format."""
    match = _DECISION.fullmatch(line) or _SUPERSEDED_DECISION.fullmatch(line)
    if match is None:
        return None
    groups = match.groupdict()
    superseded = None
    if groups.get("by_day") is not None:
        by_day = _date(groups["by_day"])
        if by_day is None:
            return None
        superseded = Supersession(by_day, groups["by"], tuple(citations(groups["by_citations"])))
    day = _date(match["day"])
    if day is None:
        return None
    return Decision(
        day=day,
        subject=match["subject"],
        stance=match["stance"],
        choice=match["choice"],
        who=tuple(_party(p) for p in match["who"].split(", ")),
        citations=tuple(citations(match["citations"])),
        superseded=superseded,
    )


def parse_loop(line: str) -> Loop | None:
    """The loop `line` holds, or None if it isn't one in the schema's format. A closed loop
    (`- [x]`) must say when it closed and cite what closed it; an open one must not."""
    match = _LOOP.fullmatch(line)
    if match is None or (match["mark"] == "x") != (match["closed"] is not None):
        return None
    opened = _date(match["opened"])
    due = _date(match["due"]) if match["due"] else None
    closed_on = _date(match["closed"]) if match["closed"] else None
    if opened is None or (match["due"] and due is None) or (match["closed"] and not closed_on):
        return None
    closure = Closure(closed_on, tuple(citations(match["closing"]))) if closed_on else None
    return Loop(
        owed_by=_party(match["by"]),
        owed_to=_party(match["to"]),
        what=match["what"],
        kind=match["kind"],
        opened=opened,
        due=due,
        citations=tuple(citations(match["citations"])),
        closed=closure,
    )


def _party(text: str) -> str:
    """`me`, or the slug a party's link goes to."""
    link = LINK.fullmatch(text)
    return link["slug"] if link else text


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def day_logs(tree: Path) -> dict[date, tuple[str, ...]]:
    """Each day log in the wiki at `tree`, by day: its non-blank lines, in order.

    Only regular files named for a real day count; lint reports anything else.
    """
    logs: dict[date, tuple[str, ...]] = {}
    for entry in folder_files(tree, DAYS):
        day = day_of(entry.name)
        if day is None:
            continue
        text = entry.read_text(encoding="utf-8", errors="replace")
        logs[day] = tuple(line for line in text.splitlines() if line.strip())
    return logs


def linked_days(tree: Path) -> dict[str, list[date]]:
    """For each slug linked from an observation, the distinct days it's linked on, in order,
    over every day log in the wiki at `tree`. The owner, `me`, is never linked."""
    days: dict[str, set[date]] = defaultdict(set)
    for day, lines in day_logs(tree).items():
        for line in lines:
            observation = parse_observation(line)
            for slug in observation.links if observation else ():
                if slug != OWNER:
                    days[slug].add(day)
    return {slug: sorted(linked) for slug, linked in days.items()}


def reference_counts(tree: Path) -> dict[str, int]:
    """For each slug linked from an observation, the number of distinct days it's linked on,
    over every day log in the wiki at `tree`. The owner, `me`, isn't counted."""
    return {slug: len(days) for slug, days in linked_days(tree).items()}


# Pages

type FieldValue = str | tuple[str, ...]
"""A frontmatter value: a scalar, or a list (`[a, b]`, or `- a` lines below the key)."""


class PageFormatError(ValueError):
    """A page whose frontmatter can't be read at all."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


@dataclass(frozen=True)
class Section:
    """A page's `## <name>` section and its non-blank lines, numbered from the page's top."""

    name: str
    line: int
    lines: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Page:
    """A page for a person, organization, or topic: its frontmatter and its sections."""

    path: str
    """Relative to the wiki, such as `people/david-kim.md`."""
    fields: dict[str, FieldValue]
    preamble: tuple[tuple[int, str], ...]
    """The non-blank lines between the frontmatter and the first section, such as a title."""
    sections: tuple[Section, ...]

    @property
    def folder(self) -> str:
        return self.path.partition("/")[0]

    @property
    def slug(self) -> str:
        return self.path.rpartition("/")[2].removesuffix(".md")

    @property
    def summary(self) -> str:
        value = self.fields.get("summary", "")
        return value if isinstance(value, str) else ""

    @property
    def aliases(self) -> tuple[str, ...]:
        value = self.fields.get("aliases", ())
        return value if isinstance(value, tuple) else ()

    @property
    def page_type(self) -> PageType:
        return PAGE_TYPES[self.folder]

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Its list of identifiers (a person's `emails`, an organization's `domains`), as
        written; none for a topic, or when the field isn't a list (lint reports that)."""
        key = self.page_type.list_field
        value = self.fields.get(key, ()) if key else ()
        return value if isinstance(value, tuple) else ()

    @property
    def title(self) -> str | None:
        """Its name, from the `# <name>` line before its sections, if it has one."""
        if self.preamble and self.preamble[0][1].startswith("# "):
            return self.preamble[0][1].removeprefix("# ").strip()
        return None

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    @property
    def decisions(self) -> list[Decision]:
        """The well-formed lines of its Decisions section."""
        return [d for d in map(parse_decision, self._lines(DECISIONS)) if d is not None]

    @property
    def loops(self) -> list[Loop]:
        """The well-formed lines of its Open loops section, open and closed."""
        return [loop for loop in map(parse_loop, self._lines(OPEN_LOOPS)) if loop is not None]

    def _lines(self, name: str) -> list[str]:
        section = self.section(name)
        return [line for _, line in section.lines] if section else []


def read_page(path: str, text: str) -> Page:
    """The page at `path` (relative to the wiki) with content `text`.

    Raises PageFormatError when its frontmatter can't be read; whether the page follows its
    type's schema is lint's to say.
    """
    lines = text.splitlines()
    fields, start = read_frontmatter(lines)
    preamble: list[tuple[int, str]] = []
    sections: list[Section] = []
    heading: tuple[int, str] | None = None
    body: list[tuple[int, str]] = []
    for number, line in enumerate(lines[start:], start=start + 1):
        if line.startswith("## "):
            if heading is not None:
                sections.append(Section(heading[1], heading[0], tuple(body)))
            heading, body = (number, line.removeprefix("## ").strip()), []
        elif line.strip():
            (body if heading is not None else preamble).append((number, line))
    if heading is not None:
        sections.append(Section(heading[1], heading[0], tuple(body)))
    return Page(path, fields, tuple(preamble), tuple(sections))


_FIELD = re.compile(r"(?P<key>[a-z_]+):(?: (?P<value>.*))?")
_BLOCK_ITEM = re.compile(r"\s*- (?P<value>.*)")


def read_frontmatter(lines: list[str]) -> tuple[dict[str, FieldValue], int]:
    """The fields of the frontmatter at the top of `lines`, and the index of the first line
    after it. Raises PageFormatError if there's none, or it can't be read."""
    if not lines or lines[0] != "---":
        raise PageFormatError("a page starts with frontmatter, between `---` lines", 1)
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise PageFormatError("the frontmatter is never closed with `---`", 1) from None
    fields: dict[str, FieldValue] = {}
    index = 1
    while index < end:
        line, number = lines[index], index + 1
        index += 1
        if not line.strip():
            continue
        match = _FIELD.fullmatch(line.rstrip())
        if match is None:
            raise PageFormatError(
                f"expected `<field>: <value>` in frontmatter, got {line!r}", number
            )
        key, value = match["key"], (match["value"] or "").strip()
        if key in fields:
            raise PageFormatError(f"`{key}` appears twice in the frontmatter", number)
        if value.startswith("["):
            if not value.endswith("]"):
                raise PageFormatError(f"`{key}` has an unclosed list: {value!r}", number)
            inner = value[1:-1].strip()
            fields[key] = tuple(_scalar(item) for item in inner.split(",")) if inner else ()
        elif value:
            fields[key] = _scalar(value)
        else:
            items: list[str] = []
            while index < end and (item := _BLOCK_ITEM.fullmatch(lines[index].rstrip())):
                items.append(_scalar(item["value"]))
                index += 1
            fields[key] = tuple(items)
    return fields, end + 1


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def pages(tree: Path) -> list[Page]:
    """Every readable page in the wiki at `tree`, by folder, then slug."""
    found: list[Page] = []
    for folder in PAGE_FOLDERS:
        for entry in folder_files(tree, folder):
            if not is_page_name(entry.name):
                continue
            text = entry.read_text(encoding="utf-8", errors="replace")
            try:
                found.append(read_page(f"{folder}/{entry.name}", text))
            except PageFormatError:
                continue
    return found


# Proposals

OPEN, ANSWERED = "Open", "Answered"
"""The sections of `proposals.md` (`## <name>`), in order."""
PROPOSAL_KINDS = {"change": ("accepted", "declined"), "question": ("answered",)}
"""Each kind of proposal, and how one of that kind can be answered: a proposed change is
accepted or declined, and a question is answered."""
PROPOSAL_TEXT_LIMIT = 200
"""The most characters a proposal's text may have: a proposal is a short question in the
librarian's own words, never a paragraph of someone else's."""
_PROPOSAL = re.compile(
    rf"- \[(?P<mark>[ x])\] (?P<id>P[1-9][0-9]*) · (?P<kind>{'|'.join(PROPOSAL_KINDS)}) · "
    rf"(?P<text>\S.*?) · proposed (?P<proposed>{_DATE})"
    rf"(?: · (?P<how>accepted|declined|answered) (?P<answered>{_DATE}))? (?P<citations>{_CITES})"
)
PROPOSAL_FORMAT = (
    "- [ ] P<number> · <change|question> · <text> · proposed YYYY-MM-DD [ep:<id>], answered as "
    "- [x] P<number> · <change|question> · <text> · proposed YYYY-MM-DD · "
    "<accepted|declined|answered> YYYY-MM-DD [ep:<id>] [ep:<the owner's answer>]"
)


@dataclass(frozen=True)
class Answer:
    """How the owner answered a proposal, and when."""

    how: str
    """accepted or declined (a proposed change), or answered (a question)."""
    day: date


@dataclass(frozen=True)
class Proposal:
    """One line of `proposals.md`: something the librarian asks the owner, open or answered."""

    id: str
    """`P` and a number, such as `P12`: its own for good, never given to another proposal."""
    kind: str
    """change (a proposed change, answered yes or no) or question (one only the owner can
    answer)."""
    text: str
    proposed: date
    citations: tuple[str, ...]
    """The episodes its line cites, in order: the episodes behind it and, once it's answered,
    the owner's answer last."""
    answer: Answer | None = None

    @property
    def number(self) -> int:
        return int(self.id[1:])

    @property
    def is_open(self) -> bool:
        return self.answer is None

    @property
    def links(self) -> tuple[str, ...]:
        """The slugs its text links, in order."""
        return tuple(links(self.text))

    @property
    def line(self) -> str:
        """Its line in `proposals.md`, exactly as `parse_proposal` read it."""
        answered = f" · {self.answer.how} {self.answer.day.isoformat()}" if self.answer else ""
        return (
            f"- [{'x' if self.answer else ' '}] {self.id} · {self.kind} · {self.text} · proposed "
            f"{self.proposed.isoformat()}{answered} "
            + " ".join(f"[ep:{episode}]" for episode in self.citations)
        )

    def to_json(self) -> dict[str, object]:
        """The proposal as a session is given it."""
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "proposed": self.proposed.isoformat(),
            "episodes": list(self.citations),
        }


def parse_proposal(line: str) -> Proposal | None:
    """The proposal `line` holds, or None if it isn't one in the schema's format.

    An answered proposal (`- [x]`) says how it was answered, in its kind's words, and when, and
    cites the owner's answer after the episodes it already cited; an open one says neither.
    The text holds no ` · `.
    """
    match = _PROPOSAL.fullmatch(line)
    if match is None or (match["mark"] == "x") != (match["how"] is not None):
        return None
    cited = tuple(citations(match["citations"]))
    proposed = _date(match["proposed"])
    if proposed is None or " · " in match["text"]:
        return None
    answer = None
    if match["how"] is not None:
        day = _date(match["answered"])
        if day is None or match["how"] not in PROPOSAL_KINDS[match["kind"]] or len(cited) < 2:
            return None
        answer = Answer(match["how"], day)
    return Proposal(match["id"], match["kind"], match["text"], proposed, cited, answer)


def read_proposals(tree: Path) -> tuple[Proposal, ...]:
    """Every well-formed proposal in `proposals.md` of the wiki at `tree`, open and answered,
    in the file's order; none when the wiki has no such file. Lint reports every other line."""
    file = tree / PROPOSALS
    if not file.is_file() or file.is_symlink():
        return ()
    text = file.read_text(encoding="utf-8", errors="replace")
    return tuple(p for p in map(parse_proposal, text.splitlines()) if p is not None)


def open_proposals(tree: Path) -> tuple[Proposal, ...]:
    """The proposals the owner hasn't answered yet, in the wiki at `tree`, in the file's
    order."""
    return tuple(proposal for proposal in read_proposals(tree) if proposal.is_open)
