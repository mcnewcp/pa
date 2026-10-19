---
status: superseded by ADR-0006
---

# The claims graph is built by our own librarian and stored in LadybugDB

We build L2 with our own librarian and schema, not with Graphiti, and store it at home in LadybugDB, an embedded graph database (the maintained fork of Kuzu). Graphiti decides who said what with LLM calls, has no field for a span within an episode, and dedups and invalidates against whatever is already in the graph, so its results depend on ingest order and nothing can be cached per episode: every rebuild would re-run every model call (5–15 per email), which breaks the change, rebuild, eval loop of principle 2 and pushes extraction onto API billing. Our main question type is attribution, which comes from source metadata, not from an LLM's entity-to-entity fact edges. None of this would show in an eval score, so the roadmap's scored spike could not have changed the answer, and it was dropped. We chose a graph database over graph-shaped tables in SQLite because we expect open-ended questions ("how is X connected to Y", paths of unknown depth) that Cypher expresses directly, and because visualizing the graph is a requirement, both for debugging the librarian (bad merges, missing links) and for discovery, and a graph database comes with an explorer.

## Considered Options

- **Graphiti with custom types**, on FalkorDB Lite or Kuzu. Rejected for the reasons above; also, Kuzu is deprecated in Graphiti, FalkorDB Lite runs a Redis subprocess that likely can't open a read-only mount, and Graphiti defaults to OpenAI for embeddings and reranking.
- **SQLite node and edge tables, with NetworkX for traversals.** No new dependency and the safest file on a read-only mount, but multi-hop and open-ended questions mean recursive CTEs or loading into NetworkX, and we'd have to build our own explorer.
- **A graph server (Neo4j, FalkorDB) on the host.** Rejected: the VM reaches nothing on the devbox, and that fence stays.

## Consequences

- We borrow Graphiti's ideas, not its code path: statements are events and are never invalidated; only states (requests, commitments, calendar events) change, with their history kept. Closure and supersession candidates come from a deterministic lookup, and one small model call decides between them. Its invalidation and dedup prompts (Apache-2.0) may be adapted, with its notice kept.
- Resolution (people, topics, supersession) is its own cached layer, separate from per-episode extraction, so a rebuild re-runs only what changed.
- Work-instance parity was not a selection criterion. The work backend is chosen at Bridgestone; the engine-neutral extraction cache and the domain query layer keep that open, and they also contain the risk of depending on a young fork.
- v0.12 starts with a LadybugDB feasibility spike: a published build opens read-only from the VM's virtiofs mount (or is copied to VM-local storage when the MCP server starts), no WAL ships in a build, the MCP server picks up new builds, the explorer runs on the host read-only, and full-text and vector indexes work. If the read-only open fails and copying isn't acceptable, the graph runs as a server inside the VM loading the published build, never as a server on the host.
