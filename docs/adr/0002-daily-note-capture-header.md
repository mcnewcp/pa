# A captured daily note carries its vault path and capture time in a capture header

A daily note's episode id is its vault path plus a hash of its text, and ADR-0001 requires ids to be derived from the raw payload alone, so renormalize can recompute them from `l0/raw/`. A note file's bytes don't contain its own path, and its L0 file name is the episode id. So the captured raw payload is the note's bytes behind a short header of `Name: value` lines and a blank line. The header has the same shape as an email's headers. The note's day comes from the date in its file name, and the version component hashes only the note's bytes after the header.

The header has two required fields:

- `Vault-Path`: the note's path in the vault, for example `Daily/2026-10-05.md`.
- `Captured-At`: when the collector read this version of the note, as an ISO 8601 time with a UTC offset, for example `2026-10-06T01:00:00-05:00`. It is the envelope's `captured_at`.

```
Vault-Path: Daily/2026-10-05.md
Captured-At: 2026-10-06T01:00:00-05:00

## Swim lessons
...
```

## Why `Captured-At` is in the header (amended for #53)

A note corrected after capture is a second version: the same vault path, a new episode id. The librarian extracts only the latest version of each vault path, so the versions need an order, and nothing else in L0 gives one. Every version has the same `occurred_at`, the day. The `captured_at` that ingest gives is when the payload landed in L0, and ingest takes the inbox in file-name order, which can put a same-day correction before the note it corrects; an eval harness ingests the whole corpus at one time. Only the collector knows when it read each version, so it writes that down in the payload, where renormalize can read it again.

## Considered Options

- **Inject the path into the note's YAML frontmatter.** Rejected: it rewrites the owner's own properties block, and a note that already has frontmatter would need merging rather than prefixing.
- **Carry the path in the inbox file name or the L0 path.** Rejected: the inbox name is lost once the payload lands, and the raw file in L0 is named by episode id, so renormalize could no longer recompute the id.
- **Order versions by the `captured_at` ingest gives, ties broken by episode id.** Rejected: in an eval build every episode has the same `captured_at`, so the order is effectively random.
- **Read `Captured-At` from the raw payload in the librarian, outside this contract.** Rejected: the ordering would rest on a header no collector is obliged to write.

## Consequences

- The corpus generator and the future Obsidian collector must write both fields. A note without either is malformed and goes to quarantine. Other header fields are ignored, so either writer can add fields later without breaking older payloads.
- `Captured-At` is a volatile field like every `captured_at`: it is not in the version hash or the `content_hash`, so a re-capture of an unchanged note is the same episode with the same content.
- Versions of a note are ordered by `captured_at`, then episode id. The librarian extracts the latest version of each vault path only, and the build report counts the versions it left out as superseded.
- A note's `occurred_at` is its day from midnight to midnight UTC, because neither the note nor the vault records a time zone.
- The owner, from instance configuration, is the note's author, so a note's envelope and `content_hash` depend on that configuration as well as the raw payload. Changing the owner's name or first email address makes a re-capture of an already ingested note go to quarantine. Run renormalize after changing it.
- A data root ingested before `Captured-At` was required has notes whose `captured_at` is when ingest landed them. Run `pa renormalize` once to take every note's `captured_at` from its header; until then, the librarian orders those versions by the old values.
