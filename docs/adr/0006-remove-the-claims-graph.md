# The claims graph is removed, and the assistant answers from files until the wiki

v0.12's claims graph didn't meet its prototype criterion ([verdict](../../evals/baselines/v0.12/verdict.md)). On the scale corpus, over 3 runs each with Sonnet answering and judging, files alone scored 0.94 overall. The graph plus files scored 0.88 to 0.89, and query only 0.88 to 0.91. `pa-eval compare` counts every one of those drops as larger than the run-to-run spread, though the Sonnet graph's only just. With Sonnet extraction, timeline fell from 1.00 to 0.80, also beyond the spread. With the graph available, the assistant took a graph result as the whole answer, and each answer cost 22 to 28% more at the median. So we remove the claims graph, its librarian, LadybugDB, the PA MCP server, and Ladybug Explorer, and the assistant answers from L0's files alone until the wiki milestone. That milestone is judged against the files-alone baseline (`evals/baselines/v0.12/scale-files-alone/`). This supersedes [ADR-0005](0005-own-librarian-ladybugdb-graph.md). The graph version is kept at the tag `archive/v0.12-claims-graph`.

## Considered Options

- **Keep the graph and fix what the verdict found** (topic splits on Sonnet, loops that never close, asks that aren't extracted, the size budget). Rejected: files alone already scores 1.00 on timeline and attribution, so even a fixed graph had little to win there, and every answer would still pay the extra turns and cost.
- **Keep the graph on main, switched off.** Rejected: the builder, server, explorer and their deploy assets would need upkeep for no consumer, and the tag keeps them recoverable.

## Consequences

- The Captured-At contract stays ([ADR-0002](0002-daily-note-capture-header.md)): the corpus generator writes it, and the v0.13 Obsidian collector must too. So does `pa renormalize`.
- The deterministic episode selection stays in `pa_core.selection`, for the wiki's intake: Gmail category, the latest version of a daily note, and a chat's owner turns.
- The eval work stays: the scale corpus, multi-run comparisons with cost, turns and duration, and the configuration seam, now with files alone as its only configuration. The v0.12 runs and verdict stay in `evals/baselines/v0.12/` as a record.
- The VM shares only L0 and its inbox directory again, and the agent project declares no MCP server.
