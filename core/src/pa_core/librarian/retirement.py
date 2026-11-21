"""Retirement: the pages dreaming cleared, and how they stay cleared (spec #140).

Dreaming retires a page the owner has no use for: a person's or an organization's the owner
never engaged with, with nothing owed either way, or an orphan. The day logs keep every fact
and git keeps the page; the wiki keeps only the slug, in `retired.md` at its root, with the
day it was retired:

    - shipping-updates · retired 2026-11-22

The host alone writes that file. A dreaming session retires a page by emptying it and naming
its slug in an applied outcome (`pa_core.librarian.dreaming`), and the host removes the page
and lists the slug. A link to a retired slug stays valid, like a link to any slug without a
page, and no day log is rewritten.

A retired slug **stands** until the owner engages with it: while no observation linking it
cites an episode of the owner's on or after the day it was retired, it isn't at the page
threshold, whatever its reference count, and lint doesn't warn that it has no page. Engagement
on the retirement day itself counts: a dreaming run is over early in its day, and what the
owner writes later that day is observed under the same date.

A retired slug gets a page again as any slug does, in a nightly run or a dreaming one: once it
is engaged since its retirement and at the threshold, when a loop or decision names it, or
when the owner asks for it. Whichever run finds it with a page again drops it from the list.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date
from pathlib import Path

from pa_core.librarian.links import alias_targets
from pa_core.librarian.wiki import RETIRED, Retirement, pages, parse_retirement

TITLE = "Retired"
"""The title the host gives `retired.md` when it starts the file."""


def standing(
    retired: Mapping[str, date], engaged_days: Mapping[str, Sequence[date]]
) -> frozenset[str]:
    """The slugs among `retired` (each with the day it was retired) that stay retired: those
    the owner hasn't engaged with on or since that day, by `engaged_days`
    (`pa_core.librarian.engagement`)."""
    return frozenset(
        slug
        for slug, since in retired.items()
        if not any(day >= since for day in engaged_days.get(slug, ()))
    )


def update_retired(tree: Path, *, today: date, retiring: Collection[str] = ()) -> None:
    """Brings `retired.md` of the wiki at `tree` up to date, as the host does at the end of
    every run: lists the slugs a dreaming run is `retiring` as retired `today`, and drops
    every slug that has a page again, or has become an alias of one (a merge, which the links
    follow). A wiki with nothing retired has no such file.

    It changes only those lines. Any other line stays as it is, for lint to fail if it isn't
    a retirement, so a file someone edited badly is put right by a person, never rewritten.
    """
    file = tree / RETIRED
    if file.is_symlink() or (file.exists() and not file.is_file()):
        # Not a file of the wiki's layout, which lint fails.
        return
    was = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
    returned = {page.slug for page in pages(tree)} | alias_targets(tree).keys()
    gone = returned | set(retiring)
    lines = [
        line
        for line in (was or "").splitlines()
        if (listed := parse_retirement(line)) is None or listed.slug not in gone
    ]
    new = [Retirement(slug, today).line for slug in sorted(set(retiring) - returned)]
    if new and not lines:
        lines = [f"# {TITLE}", ""]
    lines += new
    if all(not line.strip() or line == f"# {TITLE}" for line in lines):
        file.unlink(missing_ok=True)
    elif (text := "".join(f"{line}\n" for line in lines)) != was:
        file.write_text(text, encoding="utf-8")
