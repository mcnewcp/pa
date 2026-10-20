"""The librarian's deterministic side, shared by every instance (ADR-0007).

- `wiki`: the wiki's layout and line formats.
- `batch`: which episodes a run offers the librarian, each with its day.
- `lint`: what may be committed to the wiki.
- `run`: one librarian run, over the two ports an adapter provides (a session runner and the
  wiki repository).
- `version`: the librarian version a run records.
"""
