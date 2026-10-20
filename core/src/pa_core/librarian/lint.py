"""Lint: what may be committed to the wiki.

An error fails the run. The rules so far:

- **Layout.** Nothing but `index.md` and Markdown files directly in `days/`, `people/`,
  `orgs/`, and `topics/`: no dotfiles, symbolic links, subdirectories, other files, or any
  `CLAUDE.md`, so nothing in the wiki can be loaded as instructions or run as code (ADR-0007).
  A day log is named for its day (`YYYY-MM-DD.md`), a page for its slug (`<slug>.md`).
- **Day logs** hold only observation lines (and blank lines).
- **Pages** follow their type's schema (`wiki.py`): frontmatter with exactly the type's fields
  and valid values; the type's sections, in order, with only a `# <title>` before them;
  well-formed decision and loop lines in Decisions and Open loops; and a citation on every
  other line.
- **Citations** name episodes that are in L0.
- **Identifiers.** A person page's `emails` and an organization page's `domains` are in L0's
  metadata (any episode's participants), and no address is on two pages.

A warning doesn't fail the run; it's recorded with it, and handed to the next consolidation:

- **Orphan pages**, which nothing in the wiki links to.
- **Aging loops**: open past their due date, or open longer than the loop age (30 days unless
  configured otherwise).
- **Duplicate candidates**: pages sharing an alias, or with near-identical slugs.
- **Slugs without pages**: linked on enough days to reach the page threshold, but with no page.
- **"Needs lookup" markers**, until the research worker exists.

Each rule is a function over the wiki's files that yields findings; add a rule by writing one
and listing it in `_RULES` (rules about the layout itself live in `_layout`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pa_core.envelope import Envelope
from pa_core.librarian.wiki import (
    DAY_LOG_NAME,
    DAYS,
    DECISION_FORMAT,
    DECISIONS,
    FOLDERS,
    INDEX,
    LINK,
    LOOP_FORMAT,
    NEEDS_LOOKUP,
    OPEN_LOOPS,
    OWNER,
    PAGE_NAME,
    PAGE_TYPES,
    FieldValue,
    Page,
    PageFormatError,
    PageType,
    citations,
    parse_decision,
    parse_loop,
    parse_observation,
    read_page,
)


@dataclass(frozen=True)
class Finding:
    """One problem lint found, where it is in the wiki."""

    path: str
    """The file, relative to the wiki."""
    message: str
    line: int | None = None
    """The line, counting from 1, when the problem is on one."""

    def __str__(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class LintReport:
    errors: tuple[Finding, ...]
    """Problems that fail the run."""
    warnings: tuple[Finding, ...] = ()
    """Problems that don't: recorded with the run, and handed to the next consolidation."""


@dataclass(frozen=True)
class L0Metadata:
    """What lint checks the wiki's evidence and identifiers against: L0's episodes and the
    addresses of their participants, every episode included, even those never offered."""

    episode_ids: frozenset[str]
    addresses: frozenset[str]
    """Every participant's address, in lowercase."""

    @classmethod
    def of(cls, envelopes: Iterable[Envelope]) -> L0Metadata:
        ids: set[str] = set()
        addresses: set[str] = set()
        for envelope in envelopes:
            ids.add(envelope.episode_id)
            addresses.update(
                p.identifier.strip().lower() for p in envelope.participants if "@" in p.identifier
            )
        return cls(frozenset(ids), frozenset(addresses))

    @property
    def domains(self) -> frozenset[str]:
        """The mail domain of every address, as seen (subdomains included)."""
        return frozenset(a.rpartition("@")[2] for a in self.addresses)


@dataclass(frozen=True)
class WikiFile:
    """A Markdown file in the wiki's layout, and its lines."""

    path: str
    """Relative to the wiki, such as `days/2026-10-05.md`."""
    lines: tuple[str, ...]

    @property
    def is_day_log(self) -> bool:
        return self.path.startswith(f"{DAYS}/")


type Rule = Callable[[WikiFile, L0Metadata], Iterator[Finding]]
"""A check of one file, given L0's metadata."""


@dataclass(frozen=True)
class Thresholds:
    """What the warnings measure against."""

    today: date
    """The day the run is told it is, in the owner's time zone."""
    page_threshold: int
    """The distinct days a slug is linked on before it should have a page."""
    loop_age_days: int
    """How long a loop may stay open before it's flagged."""


def lint(tree: Path, *, l0: L0Metadata, thresholds: Thresholds) -> LintReport:
    """Lints the wiki at `tree` against `l0`, the metadata of every episode in L0."""
    files: list[WikiFile] = []
    errors = list(_layout(tree, files))
    for file in files:
        for rule in _RULES:
            errors.extend(rule(file, l0))
    pages = _readable_pages(files)
    errors.extend(_identifiers(pages, l0))
    warnings = [
        *_orphans(files, pages),
        *_aging_loops(pages, thresholds),
        *_duplicate_candidates(pages),
        *_slugs_without_pages(files, pages, thresholds),
        *_needs_lookup(files),
    ]
    return LintReport(errors=tuple(errors), warnings=tuple(warnings))


_NOT_IN_LAYOUT = (
    "isn't part of the wiki's layout: only index.md and Markdown in days/, people/, orgs/, "
    "and topics/"
)


def _layout(tree: Path, files: list[WikiFile]) -> Iterator[Finding]:
    """Findings about anything outside the layout; the files inside it go into `files`."""
    for entry in sorted(tree.iterdir()):
        if entry.name == INDEX and entry.is_file() and not entry.is_symlink():
            yield from _read(entry, INDEX, files)
        elif entry.name in FOLDERS and entry.is_dir() and not entry.is_symlink():
            yield from _folder(entry, files)
        else:
            yield _misplaced(entry, entry.name)


def _folder(folder: Path, files: list[WikiFile]) -> Iterator[Finding]:
    for entry in sorted(folder.iterdir()):
        path = f"{folder.name}/{entry.name}"
        if entry.is_symlink() or not entry.is_file():
            yield _misplaced(entry, path)
        elif entry.name.lower() == "claude.md":
            yield Finding(path, "a CLAUDE.md is never allowed in the wiki")
        elif folder.name == DAYS and not _is_day_log_name(entry.name):
            yield Finding(path, "isn't a day log: day logs are named YYYY-MM-DD.md")
        elif folder.name != DAYS and not PAGE_NAME.fullmatch(entry.name):
            yield Finding(path, "isn't a page: pages are named <slug>.md")
        else:
            yield from _read(entry, path, files)


def _misplaced(entry: Path, path: str) -> Finding:
    if entry.is_symlink():
        return Finding(path, "is a symbolic link; the wiki holds only files")
    if entry.name.lower() == "claude.md":
        return Finding(path, "a CLAUDE.md is never allowed in the wiki")
    if entry.is_dir() and path.count("/"):
        return Finding(path, "is a directory; the wiki's folders hold only Markdown files")
    return Finding(path, _NOT_IN_LAYOUT)


def _is_day_log_name(name: str) -> bool:
    if not DAY_LOG_NAME.fullmatch(name):
        return False
    try:
        date.fromisoformat(name.removesuffix(".md"))
    except ValueError:
        return False
    return True


def _read(entry: Path, path: str, files: list[WikiFile]) -> Iterator[Finding]:
    try:
        text = entry.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        yield Finding(path, "isn't UTF-8 text")
        return
    files.append(WikiFile(path, tuple(text.splitlines())))


def _observations(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """A day log holds only observation lines."""
    if not file.is_day_log:
        return
    for number, line in enumerate(file.lines, start=1):
        if line.strip() and parse_observation(line) is None:
            yield Finding(
                file.path,
                "malformed observation: expected `- <one line linking a [[slug]]> "
                f"[ep:<episode_id>]`, got {line!r}",
                number,
            )


def _cited_episodes_exist(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """Every citation names an episode in L0."""
    for number, line in enumerate(file.lines, start=1):
        for episode_id in citations(line):
            if episode_id not in l0.episode_ids:
                yield Finding(file.path, f"cites {episode_id}, which isn't in L0", number)


def _pages(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """A page follows its type's schema: frontmatter, sections, and line formats."""
    folder = file.path.partition("/")[0]
    if folder not in PAGE_TYPES:
        return
    try:
        page = read_page(file.path, "\n".join(file.lines))
    except PageFormatError as error:
        yield Finding(file.path, f"bad frontmatter: {error.message}", error.line)
        return
    page_type = PAGE_TYPES[folder]
    for problem in _frontmatter_problems(page.fields, page_type):
        yield Finding(file.path, f"bad frontmatter: {problem}", 1)
    yield from _sections(page, page_type)
    yield from _section_lines(page)


def _frontmatter_problems(fields: Mapping[str, FieldValue], page_type: PageType) -> Iterator[str]:
    identifiers = page_type.list_field
    expected = page_type.fields
    for key in expected:
        if key not in fields:
            yield f"`{key}` is missing"
    for key in fields:
        if key not in expected:
            yield f"`{key}` isn't a field of a {page_type.type} page"
    if "type" in fields and fields["type"] != page_type.type:
        yield (
            f"`type` is {fields['type']!r}, but pages in {page_type.folder}/ are "
            f"{page_type.type} pages"
        )
    statuses = page_type.statuses
    if "status" in fields and fields["status"] not in statuses:
        yield f"`status` is {fields['status']!r}, not one of {', '.join(statuses)}"
    summary = fields.get("summary")
    if "summary" in fields and not (isinstance(summary, str) and summary):
        yield "`summary` must be one line of text"
    updated = fields.get("updated")
    if "updated" in fields and not (isinstance(updated, str) and _is_date(updated)):
        yield f"`updated` must be a date, YYYY-MM-DD, not {updated!r}"
    for key in ("aliases", *([identifiers] if identifiers else [])):
        if key in fields and not isinstance(fields[key], tuple):
            yield f"`{key}` must be a list, such as [a, b] or []"


def _sections(page: Page, page_type: PageType) -> Iterator[Finding]:
    expected = page_type.sections
    names = tuple(section.name for section in page.sections)
    if names != expected:
        line = page.sections[0].line if page.sections else None
        yield Finding(
            page.path,
            f"wrong sections for a {page_type.type} page: expected "
            f"{', '.join(f'`## {name}`' for name in expected)}, in that order; got "
            f"{', '.join(f'`## {name}`' for name in names) or 'none'}",
            line,
        )
    for number, line in page.preamble:
        if number != page.preamble[0][0] or not line.startswith("# "):
            yield Finding(
                page.path,
                f"text before its first section: only a `# <title>` line may come before "
                f"the sections, got {line!r}",
                number,
            )


def _section_lines(page: Page) -> Iterator[Finding]:
    for section in page.sections:
        for number, line in section.lines:
            if section.name == DECISIONS and parse_decision(line) is None:
                yield Finding(
                    page.path,
                    f"malformed decision: expected `{DECISION_FORMAT}`, got {line!r}",
                    number,
                )
            elif section.name == OPEN_LOOPS and parse_loop(line) is None:
                yield Finding(
                    page.path, f"malformed loop: expected `{LOOP_FORMAT}`, got {line!r}", number
                )
            elif (
                section.name not in (DECISIONS, OPEN_LOOPS)
                and not line.startswith("#")
                and not citations(line)
            ):
                yield Finding(
                    page.path,
                    "a line without a citation: every line cites the episodes it came from, as "
                    f"[ep:<episode_id>]; got {line!r}",
                    number,
                )


def _readable_pages(files: Iterable[WikiFile]) -> list[Page]:
    """The pages among `files` whose frontmatter can be read."""
    found: list[Page] = []
    for file in files:
        if file.path.partition("/")[0] in PAGE_TYPES:
            try:
                found.append(read_page(file.path, "\n".join(file.lines)))
            except PageFormatError:
                continue
    return found


def _identifiers(pages: Iterable[Page], l0: L0Metadata) -> Iterator[Finding]:
    """A page's addresses and domains are in L0's metadata, and an address is on one page."""
    listed: dict[str, str] = {}
    """The page listing each address first."""
    for page in pages:
        key = PAGE_TYPES[page.folder].list_field
        values = page.fields.get(key, ()) if key else ()
        if not isinstance(values, tuple):
            continue  # reported as bad frontmatter
        known = l0.addresses if key == "emails" else l0.domains
        for value in values:
            identifier = value.strip().lower()
            if identifier not in known:
                yield Finding(
                    page.path,
                    f"`{key}` lists {value}, which isn't in L0's metadata: identifiers are "
                    "copied from the identity table, never made up",
                    1,
                )
            if key == "emails":
                first = listed.setdefault(identifier, page.path)
                if first != page.path:
                    yield Finding(
                        page.path,
                        f"`emails` lists {value}, which {first} lists too: an address belongs "
                        "on one page",
                        1,
                    )


# Warnings


def _orphans(files: Sequence[WikiFile], pages: Sequence[Page]) -> Iterator[Finding]:
    """A page nothing else in the wiki links to (the index aside)."""
    linked_from: dict[str, set[str]] = defaultdict(set)
    for file in files:
        if file.path != INDEX:
            for line in file.lines:
                for slug in LINK.findall(line):
                    linked_from[slug].add(file.path)
    for page in pages:
        if not linked_from[page.slug] - {page.path}:
            yield Finding(page.path, f"an orphan page: nothing in the wiki links [[{page.slug}]]")


def _aging_loops(pages: Sequence[Page], thresholds: Thresholds) -> Iterator[Finding]:
    """An open loop past its due date, or open longer than the loop age."""
    for page in pages:
        section = page.section(OPEN_LOOPS)
        for number, line in section.lines if section else ():
            loop = parse_loop(line)
            if loop is None or not loop.is_open:
                continue
            age = (thresholds.today - loop.opened).days
            if loop.due is not None and loop.due < thresholds.today:
                late = (thresholds.today - loop.due).days
                yield Finding(
                    page.path, f"a loop past due: due {loop.due}, {late} days ago", number
                )
            elif age > thresholds.loop_age_days:
                yield Finding(
                    page.path,
                    f"a loop open {age} days, more than {thresholds.loop_age_days}: is it "
                    "settled, or still owed?",
                    number,
                )


def _duplicate_candidates(pages: Sequence[Page]) -> Iterator[Finding]:
    """Two pages that may be one: a shared alias, or near-identical slugs."""
    aliased: dict[str, str] = {}
    """The first page with each alias, by its folded form."""
    for page in pages:
        for alias in dict.fromkeys(" ".join(a.split()).casefold() for a in page.aliases):
            first = aliased.setdefault(alias, page.path)
            if first != page.path:
                yield Finding(
                    page.path, f"a duplicate candidate: it shares the alias {alias} with {first}"
                )
    for index, page in enumerate(pages):
        for other in pages[:index]:
            if _nearly(page.slug, other.slug):
                yield Finding(
                    page.path, f"a duplicate candidate: its slug is nearly {other.path}'s"
                )


def _nearly(first: str, second: str) -> bool:
    """Whether two slugs are the same but for hyphens, or one letter (`david-kim-2`)."""
    a, b = first.replace("-", ""), second.replace("-", "")
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = sorted((a, b), key=len)
    index = next(
        (i for i, (x, y) in enumerate(zip(short, long, strict=False)) if x != y), len(short)
    )
    return short[index:] == long[index + 1 :]


def _slugs_without_pages(
    files: Sequence[WikiFile], pages: Sequence[Page], thresholds: Thresholds
) -> Iterator[Finding]:
    """A slug linked on enough days to need a page that has none."""
    days: dict[str, list[str]] = defaultdict(list)
    """The day logs linking each slug, in order."""
    for file in files:
        if not file.is_day_log:
            continue
        for line in file.lines:
            observation = parse_observation(line)
            for slug in observation.links if observation else ():
                if slug != OWNER and file.path not in days[slug]:
                    days[slug].append(file.path)
    with_pages = {page.slug for page in pages}
    for slug, logs in sorted(days.items()):
        if len(logs) >= thresholds.page_threshold and slug not in with_pages:
            yield Finding(
                logs[-1],
                f"`{slug}` is linked on {len(logs)} days, reaching the page threshold of "
                f"{thresholds.page_threshold}, but has no page",
            )


def _needs_lookup(files: Sequence[WikiFile]) -> Iterator[Finding]:
    """An external fact still marked as needing a lookup."""
    for file in files:
        if file.path == INDEX:
            continue
        for number, line in enumerate(file.lines, start=1):
            for match in NEEDS_LOOKUP.finditer(line):
                yield Finding(file.path, f"needs lookup: {match['question']}", number)


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


_RULES: tuple[Rule, ...] = (_observations, _pages, _cited_episodes_exist)
