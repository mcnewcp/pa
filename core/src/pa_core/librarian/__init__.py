"""The librarian's deterministic side, shared by every instance (ADR-0007).

- `wiki`: the wiki model: its layout, slugs, links, citations, and line formats.
- `batch`: which episodes a run offers the librarian, each with its day.
- `identity`: the identity table, who's who by address, from L0's metadata.
- `lint`: what may be committed to the wiki.
- `index`: `index.md`, generated from the pages.
- `links`: links to an alias rewritten to its page.
- `run`: one librarian run, over the two ports an adapter provides (a session runner and the
  wiki repository).
- `replay`: L0 run through the librarian a week at a time, into a fresh wiki.
- `version`: the librarian version a run records.
"""
