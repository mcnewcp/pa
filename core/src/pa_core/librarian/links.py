"""Link rewrites: links to an alias become links to its page, across the whole wiki.

After a merge, the merged-away name lives on as an alias of the page it was merged into, and
the day logs and pages still link it. Once a run's sessions pass, the host rewrites every
`[[<alias>]]` to `[[<slug>]]`, day logs included, so no link is left stale (spec #56).

An alias links by its slug ("Okie" by `okie`). A link is rewritten only when that slug has no
page of its own and is an alias of exactly one page; the owner's `me` is never rewritten.
"""

from __future__ import annotations

from pathlib import Path

from pa_core.librarian.wiki import FOLDERS, LINK, OWNER, pages, slugify


def alias_targets(tree: Path) -> dict[str, str]:
    """Each alias slug in the wiki at `tree` that a link may be rewritten from, and the slug of
    the page it's an alias of."""
    found = pages(tree)
    slugs = {page.slug for page in found}
    claimed: dict[str, set[str]] = {}
    for page in found:
        for alias in page.aliases:
            slug = slugify(alias)
            if slug and slug != OWNER and slug not in slugs:
                claimed.setdefault(slug, set()).add(page.slug)
    return {alias: next(iter(owners)) for alias, owners in claimed.items() if len(owners) == 1}


def rewrite_alias_links(tree: Path) -> list[str]:
    """Rewrites links to an alias to its page's slug in every Markdown file of the wiki at
    `tree`, and returns the files it changed, relative to the wiki."""
    targets = alias_targets(tree)
    if not targets:
        return []
    changed: list[str] = []
    for folder in FOLDERS:
        directory = tree / folder
        if not directory.is_dir() or directory.is_symlink():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink() or not entry.is_file() or entry.suffix != ".md":
                continue
            text = entry.read_text(encoding="utf-8")
            rewritten = LINK.sub(lambda m: f"[[{targets.get(m[1], m[1])}]]", text)
            if rewritten != text:
                entry.write_text(rewritten, encoding="utf-8")
                changed.append(f"{folder}/{entry.name}")
    return changed
