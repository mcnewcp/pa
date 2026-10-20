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

Each rule is a function over the wiki's files that yields findings; add a rule by writing one
and listing it in `_RULES` (rules about the layout itself live in `_layout`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pa_core.librarian.wiki import (
    DAY_LOG_NAME,
    DAYS,
    DECISION_FORMAT,
    DECISIONS,
    FIELDS,
    FOLDERS,
    INDEX,
    LIST_FIELDS,
    LOOP_FORMAT,
    OPEN_LOOPS,
    PAGE_NAME,
    PAGE_TYPES,
    SECTIONS,
    STATUSES,
    FieldValue,
    Page,
    PageFormatError,
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


@dataclass(frozen=True)
class WikiFile:
    """A Markdown file in the wiki's layout, and its lines."""

    path: str
    """Relative to the wiki, such as `days/2026-10-05.md`."""
    lines: tuple[str, ...]

    @property
    def is_day_log(self) -> bool:
        return self.path.startswith(f"{DAYS}/")


type Rule = Callable[[WikiFile, Collection[str]], Iterator[Finding]]
"""A check of one file, given the ids of every episode in L0."""


def lint(tree: Path, *, episode_ids: Collection[str]) -> LintReport:
    """Lints the wiki at `tree`; `episode_ids` are the ids of every episode in L0."""
    files: list[WikiFile] = []
    errors = list(_layout(tree, files))
    for file in files:
        for rule in _RULES:
            errors.extend(rule(file, episode_ids))
    return LintReport(errors=tuple(errors))


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


def _observations(file: WikiFile, episode_ids: Collection[str]) -> Iterator[Finding]:
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


def _cited_episodes_exist(file: WikiFile, episode_ids: Collection[str]) -> Iterator[Finding]:
    """Every citation names an episode in L0."""
    for number, line in enumerate(file.lines, start=1):
        for episode_id in citations(line):
            if episode_id not in episode_ids:
                yield Finding(file.path, f"cites {episode_id}, which isn't in L0", number)


def _pages(file: WikiFile, episode_ids: Collection[str]) -> Iterator[Finding]:
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
    for problem in _frontmatter_problems(page.fields, page_type, folder):
        yield Finding(file.path, f"bad frontmatter: {problem}", 1)
    yield from _sections(page, page_type)
    yield from _section_lines(page)


def _frontmatter_problems(
    fields: Mapping[str, FieldValue], page_type: str, folder: str
) -> Iterator[str]:
    identifiers = LIST_FIELDS.get(page_type)
    expected = (*FIELDS, *([identifiers] if identifiers else []))
    for key in expected:
        if key not in fields:
            yield f"`{key}` is missing"
    for key in fields:
        if key not in expected:
            yield f"`{key}` isn't a field of a {page_type} page"
    if "type" in fields and fields["type"] != page_type:
        yield f"`type` is {fields['type']!r}, but pages in {folder}/ are {page_type} pages"
    statuses = STATUSES[page_type]
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


def _sections(page: Page, page_type: str) -> Iterator[Finding]:
    expected = SECTIONS[page_type]
    names = tuple(section.name for section in page.sections)
    if names != expected:
        line = page.sections[0].line if page.sections else None
        yield Finding(
            page.path,
            f"wrong sections for a {page_type} page: expected "
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


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


_RULES: tuple[Rule, ...] = (_observations, _pages, _cited_episodes_exist)
