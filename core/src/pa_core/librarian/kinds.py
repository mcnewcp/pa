"""The kinds of candidate: what the host can flag in the wiki for dreaming to judge.

One enum, below both of its users. Lint gives each warning its kind (`pa_core.librarian.lint`),
and a warning is a candidate of that kind; the rest are flagged by
`pa_core.librarian.candidates`, which lint has no warning for. A new kind is a value here and
a row in `pa_core.librarian.candidates.ORDER`.
"""

from __future__ import annotations

from enum import StrEnum


class CandidateKind(StrEnum):
    """What a candidate is about. `pa_core.librarian.candidates.ORDER` is the order the
    kinds are offered in, and the librarian project's dreaming instructions say how to judge
    each."""

    STALE = "stale"
    """A page that observations have linked on enough days since it was last updated."""
    STATUS_MISMATCH = "status-mismatch"
    """A page whose status says its matter is over while one of its loops is open."""
    ORPHAN = "orphan"
    """A page nothing in the wiki links to."""
    LOOP_PAST_DUE = "loop-past-due"
    LOOP_AGING = "loop-aging"
    """A loop open longer than the loop age, and not past due."""
    DUPLICATE = "duplicate"
    """Two pages that may be one."""
    SLUG_WITHOUT_PAGE = "slug-without-page"
    NEEDS_LOOKUP = "needs-lookup"
    QUIET = "quiet"
    """An active page no observation has linked for a long time."""
    UNENGAGED = "unengaged"
    """A person's or an organization's page the owner has never engaged with."""
