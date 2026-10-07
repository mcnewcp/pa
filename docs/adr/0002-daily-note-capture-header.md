# A captured daily note carries its vault path in a capture header

A daily note's episode id is its vault path plus a hash of its text, and ADR-0001 requires ids to be derived from the raw payload alone, so renormalize can recompute them from `l0/raw/`. A note file's bytes don't contain its own path, and its L0 file name is the episode id. So the captured raw payload is the note's bytes behind a short header of `Name: value` lines and a blank line, for example `Vault-Path: Daily/2026-10-05.md`. The header has the same shape as an email's headers. The note's day comes from the date in its file name, and the version component hashes only the note's bytes after the header.

## Considered Options

- **Inject the path into the note's YAML frontmatter.** Rejected: it rewrites the owner's own properties block, and a note that already has frontmatter would need merging rather than prefixing.
- **Carry the path in the inbox file name or the L0 path.** Rejected: the inbox name is lost once the payload lands, and the raw file in L0 is named by episode id, so renormalize could no longer recompute the id.

## Consequences

- The corpus generator and the future Obsidian collector must write this header. Unknown header fields are ignored, so either can add fields later without breaking older payloads.
- A note's `occurred_at` is its day from midnight to midnight UTC, because neither the note nor the vault records a time zone.
