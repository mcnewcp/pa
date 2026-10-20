"""The wiki model: the wiki's layout, slugs, citations, and line formats (spec #56).

The wiki is `index.md` and four folders of Markdown: `days/` (day logs, `YYYY-MM-DD.md`) and
`people/`, `orgs/` and `topics/` (pages, `<slug>.md`). Nothing else may exist in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INDEX = "index.md"
"""The generated index, the only file at the wiki's top level."""
DAYS = "days"
"""The folder of day logs."""
PAGE_FOLDERS = ("people", "orgs", "topics")
"""The folders of pages, by page type."""
FOLDERS = (DAYS, *PAGE_FOLDERS)

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
"""Lowercase ASCII words joined by hyphens: a page's name, and how it's linked."""
DAY_LOG_NAME = re.compile(r"\d{4}-\d{2}-\d{2}\.md")
PAGE_NAME = re.compile(rf"{SLUG.pattern}\.md")

CITATION = re.compile(r"\[ep:([A-Za-z0-9_-]+)\]")
"""A citation of one episode, `[ep:<episode_id>]`: plain text, never a wikilink."""
LINK = re.compile(rf"\[\[({SLUG.pattern})\]\]")
"""A link to a person, organization, or topic by slug, whether or not it has a page yet."""

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
