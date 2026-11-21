"""Candidates: the problems the host flags mechanically for dreaming to judge (spec #140).

The model's judgment is spent on judging, not on finding: before a dreaming run the host lists
what looks wrong with the wiki, and the session gives each candidate one outcome
(`pa_core.librarian.dreaming`). `candidates` is the one entry point, and nothing else knows
which problems are offered, in what order, or what each is called.

So far a candidate is a lint warning: an orphan page, a loop past due or open too long, a
duplicate candidate, an engaged slug that reached the page threshold without a page, or a
"needs lookup" marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pa_core.librarian.lint import Finding, L0Metadata, Thresholds, lint
from pa_core.owner import Owner


class CandidateKind(StrEnum):
    """What a candidate is about. The librarian project's dreaming instructions say how to
    judge each."""

    ORPHAN = "orphan"
    """A page nothing in the wiki links to."""
    LOOP_PAST_DUE = "loop-past-due"
    LOOP_AGING = "loop-aging"
    """A loop open longer than the loop age, and not past due."""
    DUPLICATE = "duplicate"
    """Two pages that may be one."""
    SLUG_WITHOUT_PAGE = "slug-without-page"
    NEEDS_LOOKUP = "needs-lookup"


@dataclass(frozen=True)
class Candidate:
    """One problem offered to a dreaming run."""

    id: str
    """What the run's outcomes call it: `c01`, `c02`, and so on, in the order offered."""
    kind: CandidateKind
    pages: tuple[str, ...]
    """The pages it's about, relative to the wiki: both pages of a duplicate, and none for a
    slug without a page."""
    path: str
    """The file the problem is in, relative to the wiki: one of `pages`, or a day log."""
    message: str
    """The problem, as lint words it."""
    line: int | None = None
    """The line in `path`, counting from 1, when the problem is on one."""
    episodes: tuple[str, ...] = ()
    """For a loop, the episodes its line cites as opening it: which loop it is, wherever its
    line moves."""
    slug: str | None = None
    """For a slug without a page, the slug."""

    @property
    def subject(self) -> str:
        """What it's about, in a line: its pages (with a loop's episodes), or its slug, or the
        file it's in."""
        if self.pages:
            cited = "".join(f" [ep:{episode}]" for episode in self.episodes)
            return ", ".join(self.pages) + cited
        return f"[[{self.slug}]]" if self.slug else self.path

    def to_json(self) -> dict[str, object]:
        """The candidate as a dreaming session is given it."""
        listed: dict[str, object] = {
            "id": self.id,
            "kind": str(self.kind),
            "pages": list(self.pages),
            "path": self.path,
        }
        if self.line is not None:
            listed["line"] = self.line
        listed["message"] = self.message
        if self.episodes:
            listed["episodes"] = list(self.episodes)
        if self.slug is not None:
            listed["slug"] = self.slug
        return listed


def candidates(
    tree: Path, *, l0: L0Metadata, owner: Owner, thresholds: Thresholds
) -> tuple[Candidate, ...]:
    """The candidates to offer a dreaming run over the wiki at `tree`, in order: every lint
    warning, measured against `l0`'s metadata and `thresholds` for `owner`.

    The ids follow from the wiki alone, so the same wiki on the same day offers the same
    candidates under the same ids.
    """
    warnings = lint(tree, l0=l0, owner=owner, thresholds=thresholds).warnings
    return tuple(
        _of_warning(f"c{number:02d}", warning) for number, warning in enumerate(warnings, start=1)
    )


def _of_warning(candidate_id: str, warning: Finding) -> Candidate:
    if warning.kind is None:
        raise ValueError(f"a lint warning without a kind can't be a candidate: {warning}")
    return Candidate(
        id=candidate_id,
        kind=CandidateKind(warning.kind.value),
        pages=warning.pages,
        path=warning.path,
        message=warning.message,
        line=warning.line,
        episodes=warning.episodes,
        slug=warning.slug,
    )
