"""Lint: what may be committed to the wiki.

An error fails the run. The rules so far:

- **Layout.** Nothing but `index.md` and Markdown files directly in `days/`, `people/`,
  `orgs/`, and `topics/`: no dotfiles, symbolic links, subdirectories, other files, or any
  `CLAUDE.md`, so nothing in the wiki can be loaded as instructions or run as code (ADR-0007).
  A day log is named for its day (`YYYY-MM-DD.md`), a page for its slug (`<slug>.md`).
- **Day logs** hold only observation lines (and blank lines).
- **Citations** name episodes that are in L0.

Each rule is a function over the wiki's files that yields findings; add a rule by writing one
and listing it in `_RULES` (rules about the layout itself live in `_layout`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pa_core.librarian.wiki import (
    DAY_LOG_NAME,
    DAYS,
    FOLDERS,
    INDEX,
    PAGE_NAME,
    citations,
    parse_observation,
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


_RULES: tuple[Rule, ...] = (_observations, _cited_episodes_exist)
