"""The wiki model: the wiki's layout, slugs, citations, and line formats (spec #56).

The wiki is `index.md` and four folders of Markdown: `days/` (day logs, `YYYY-MM-DD.md`) and
`people/`, `orgs/` and `topics/` (pages, `<slug>.md`). Nothing else may exist in it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

INDEX = "index.md"
"""The generated index, the only file at the wiki's top level."""
DAYS = "days"
"""The folder of day logs."""
PAGE_FOLDERS = ("people", "orgs", "topics")
"""The folders of pages, by page type."""
FOLDERS = (DAYS, *PAGE_FOLDERS)

PERSON, ORGANIZATION, TOPIC = "person", "organization", "topic"
PAGE_TYPES = {"people": PERSON, "orgs": ORGANIZATION, "topics": TOPIC}
"""Each page folder's page type, which its pages' `type` must be."""

CURRENT_STATE, DECISIONS, OPEN_LOOPS, NOTES = "Current state", "Decisions", "Open loops", "Notes"
SECTIONS = {
    PERSON: (CURRENT_STATE, OPEN_LOOPS, NOTES),
    ORGANIZATION: (CURRENT_STATE, OPEN_LOOPS, NOTES),
    TOPIC: (CURRENT_STATE, DECISIONS, OPEN_LOOPS, NOTES),
}
"""Each page type's sections (`## <name>`), in order. Current state and Notes are free-form."""

FIELDS = ("type", "aliases", "status", "summary", "updated")
"""The frontmatter every page has."""
LIST_FIELDS = {PERSON: "emails", ORGANIZATION: "domains"}
"""The list of identifiers a page type adds to its frontmatter: a person's email addresses, an
organization's mail domains."""
STATUSES = {
    PERSON: ("active", "inactive"),
    ORGANIZATION: ("active", "inactive"),
    TOPIC: ("active", "settled", "dormant"),
}
"""Each page type's allowed `status` values."""

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""Lowercase ASCII words joined by hyphens: a page's name, and how it's linked."""
OWNER = "me"
"""How the wiki writes the owner, who is never linked and never gets a page."""
DAY_LOG_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
PAGE_NAME = re.compile(rf"{SLUG.pattern}\.md")

CITATION = re.compile(r"\[ep:([A-Za-z0-9_-]+)\]")
"""A citation of one episode, `[ep:<episode_id>]`: plain text, never a wikilink."""
LINK = re.compile(rf"\[\[({SLUG.pattern})\]\]")
"""A link to a person, organization, or topic by slug, whether or not it has a page yet."""
NEEDS_LOOKUP = re.compile(r"\[needs lookup: (?P<question>[^\]]+)\]")
"""An external fact the librarian couldn't look up, inline where it's missing."""

_OBSERVATION = re.compile(
    rf"- (?P<text>\S.*?) (?P<citations>{CITATION.pattern}(?: {CITATION.pattern})*)"
)


@dataclass(frozen=True)
class Observation:
    """One day-log line: what one episode shows, linking at least one slug."""

    text: str
    links: tuple[str, ...]
    citations: tuple[str, ...]


def parse_observation(line: str) -> Observation | None:
    """The observation `line` holds, or None if it isn't one.

    The format is `- <one line, linking at least one [[slug]]> [ep:<id>]`, with one or more
    citations at the end.
    """
    match = _OBSERVATION.fullmatch(line)
    if match is None:
        return None
    links = tuple(LINK.findall(match["text"]))
    if not links:
        return None
    return Observation(match["text"], links, tuple(CITATION.findall(match["citations"])))


def citations(text: str) -> list[str]:
    """The episode ids `text` cites, in order."""
    return CITATION.findall(text)


# Decision and loop lines. A party is `me` or a `[[slug]]`; `<who>` is parties joined by ", ".

_DATE = r"\d{4}-\d{2}-\d{2}"
_CITES = r"\[ep:[A-Za-z0-9_-]+\](?: \[ep:[A-Za-z0-9_-]+\])*"
_PARTY = rf"(?:{OWNER}|\[\[{SLUG.pattern}\]\])"
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
    return text.removeprefix("[[").removesuffix("]]")


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def day_logs(tree: Path) -> dict[date, tuple[str, ...]]:
    """Each day log in the wiki at `tree`, by day: its non-blank lines, in order.

    Only regular files named for a real day count; lint reports anything else.
    """
    folder = tree / DAYS
    logs: dict[date, tuple[str, ...]] = {}
    if not folder.is_dir() or folder.is_symlink():
        return logs
    for entry in sorted(folder.iterdir()):
        if entry.is_symlink() or not entry.is_file() or not DAY_LOG_NAME.fullmatch(entry.name):
            continue
        try:
            day = date.fromisoformat(entry.name.removesuffix(".md"))
        except ValueError:
            continue
        text = entry.read_text(encoding="utf-8", errors="replace")
        logs[day] = tuple(line for line in text.splitlines() if line.strip())
    return logs


def reference_counts(tree: Path) -> dict[str, int]:
    """For each slug linked from an observation, the number of distinct days it's linked on,
    over every day log in the wiki at `tree`. The owner, `me`, isn't counted."""
    days: dict[str, set[date]] = defaultdict(set)
    for day, lines in day_logs(tree).items():
        for line in lines:
            observation = parse_observation(line)
            for slug in observation.links if observation else ():
                if slug != OWNER:
                    days[slug].add(day)
    return {slug: len(linked) for slug, linked in days.items()}


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
        directory = tree / folder
        if not directory.is_dir() or directory.is_symlink():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink() or not entry.is_file() or not PAGE_NAME.fullmatch(entry.name):
                continue
            text = entry.read_text(encoding="utf-8", errors="replace")
            try:
                found.append(read_page(f"{folder}/{entry.name}", text))
            except PageFormatError:
                continue
    return found
