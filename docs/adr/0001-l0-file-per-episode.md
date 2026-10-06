# L0 is one file per episode, with raw payloads as the truth

On the home instance, L0 stores each episode as a write-once JSON envelope file, with its original raw payload in a sibling file, partitioned by source and month of `occurred_at` (UTC). A SQLite catalog indexes the episodes but is derived and can be deleted and rebuilt at any time. We chose files over a database so that immutability is structural (write-once, OS read-only permissions) rather than a discipline, so that one corrupt file can't take out the store, so that the v0.10 baseline agent can search L0 with plain file tools, and so that incremental backup is trivial.

Only raw payloads are strictly immutable. Envelopes are produced by versioned normalizer code: when a normalizer changes, a renormalize command rebuilds the whole envelope tree from raw and swaps it in atomically. For this to work, `episode_id` is derived from the raw payload alone (source plus a hash of the native id and any version component), never from normalizer output.

All data lives under one configured data root (`PA_DATA_DIR`) outside the repository; the code refuses to start if that root resolves inside the repo tree. The synthetic corpus is the only exception, because it is a test fixture rather than data.

## Considered Options

- **SQLite as the source of truth.** Rejected: mutable by nature, a single file whose corruption risks everything, and not greppable by the baseline.
- **Append-only JSONL files.** Rejected: a crash mid-write corrupts the last line, duplicate checks need a separate index anyway, and raw payloads still need their own files.

## Consequences

- Idempotent ingest is a file-existence check: the same id with the same `content_hash` is a no-op, and the same id with a different hash is quarantined, never overwritten.
- `content_hash` covers the envelope's meaningful content, not raw bytes, so a re-capture that differs only in volatile fields (labels, capture time) collapses instead of being quarantined. A renormalize can change hashes, and the catalog is rebuilt with it.
- On the work instance, the same envelope schema maps to columns in an append-only Delta table behind the same L0 storage interface.
