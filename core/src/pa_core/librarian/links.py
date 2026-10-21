"""Link rewrites: links to an alias become links to its page, across the whole wiki.

After a merge, the merged-away name lives on as an alias of the page it was merged into, and
the day logs and pages still link it. Once a run's sessions pass, the host rewrites every
`[[<alias>]]` to `[[<slug>|<alias>]]`, day logs included, so no link is left stale (spec #56).
The rewritten link goes to the page but still shows the name it was written with, so a line
such as the merge's own observation, "[[okie]] is [[ben-okafor]]", keeps saying what it said:
"[[ben-okafor|okie]] is [[ben-okafor]]". A piped link keeps the text it shows.

An alias links by its slug ("Okie" by `okie`). A link is rewritten only when that slug has no
page of its own and is an alias of exactly one page; the owner's `me` is never rewritten.
"""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path

from pa_core.librarian.wiki import FOLDERS, LINK, OWNER, folder_files, pages, slugify


def alias_targets(tree: Path) -> dict[str, str]:
    """Each alias slug in the wiki at `tree` that a link may be rewritten from, and the slug of
    the page it's an alias of."""
    found = pages(tree)
    slugs = {page.slug for page in found}
    aliased_by: dict[str, set[str]] = {}
    for page in found:
        for alias in page.aliases:
            slug = slugify(alias)
            if slug and slug != OWNER and slug not in slugs:
                aliased_by.setdefault(slug, set()).add(page.slug)
    return {alias: next(iter(by)) for alias, by in aliased_by.items() if len(by) == 1}


def _rewritten(targets: dict[str, str], link: re.Match[str]) -> str:
    slug = link["slug"]
    if slug not in targets:
        return link[0]
    return f"[[{targets[slug]}|{link['shown'] or slug}]]"


def rewrite_alias_links(tree: Path) -> list[str]:
    """Rewrites links to an alias to its page's slug in every Markdown file of the wiki at
    `tree`, and returns the files it changed, relative to the wiki."""
    targets = alias_targets(tree)
    if not targets:
        return []
    changed: list[str] = []
    for folder in FOLDERS:
        for entry in folder_files(tree, folder):
            if entry.suffix != ".md":
                continue
            text = entry.read_text(encoding="utf-8")
            rewritten = LINK.sub(partial(_rewritten, targets), text)
            if rewritten != text:
                entry.write_text(rewritten, encoding="utf-8")
                changed.append(f"{folder}/{entry.name}")
    return changed
