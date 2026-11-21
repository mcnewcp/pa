"""The librarian's deterministic side, shared by every instance (ADR-0007).

- `wiki`: the wiki model: its layout, slugs, links, citations, and line formats.
- `batch`: which episodes a run offers the librarian, each with its day.
- `identity`: the identity table, who's who by address, from L0's metadata.
- `metadata`: L0's metadata, which the host checks the wiki against: the episodes, their
  participants' addresses, and whose doing each is.
- `engagement`: which slugs the owner has had a hand in, from L0's metadata.
- `kinds`: the kinds of candidate, which lint's warnings and dreaming's candidates share.
- `lint`: what may be committed to the wiki.
- `index`: `index.md`, generated from the pages.
- `links`: links to an alias rewritten to its page.
- `session`: what a librarian run and a dreaming run share: the session port, the session and
  its resume loop, tree states, and the commit message's fields.
- `run`: one librarian run, over the two ports an adapter provides (a session runner and the
  wiki repository).
- `candidates`: what the host flags in the wiki for dreaming to judge.
- `dreaming`: one dreaming run, the librarian's review of the whole wiki, over the same ports.
- `retirement`: the pages dreaming cleared: which slugs stay retired, and `retired.md`.
- `replay`: L0 run through the librarian a week at a time, into a fresh wiki.
- `dream_copy`: a dreaming run over a copy of a replay target or of a cut's wiki, at its as-of
  time.
- `version`: the librarian version a run records.
"""
