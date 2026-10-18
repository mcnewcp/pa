# Eval comparison

- ../v0.11: 1 run
- argus-graph-plus-files-sonnet: 1 run (configuration graph-plus-files)
- argus-graph-plus-files-opus: 1 run (configuration graph-plus-files)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from ../v0.11 counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | ../v0.11 | argus-graph-plus-files-sonnet | argus-graph-plus-files-opus | argus-graph-plus-files-sonnet vs ../v0.11 | argus-graph-plus-files-opus vs ../v0.11 |
|---|---|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| open_loops | 1.00 (1.00 to 1.00) | 0.50 (0.50 to 0.50) | 0.50 (0.50 to 0.50) | -0.50 | -0.50 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| entity_resolution | 0.50 (0.50 to 0.50) | 0.50 (0.50 to 0.50) | 1.00 (1.00 to 1.00) | +0.00 | +0.50 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 |
| Overall | 0.96 (0.96 to 0.96) | 0.92 (0.92 to 0.92) | 0.96 (0.96 to 0.96) | -0.04 | +0.00 |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | ../v0.11 | argus-graph-plus-files-sonnet | argus-graph-plus-files-opus |
|---|---|---|---|
| single_fact_recall | n/a | $0.080, 5 turns, 10.2 s | $0.081, 5 turns, 8.0 s |
| attribution | n/a | $0.092, 7 turns, 10.5 s | $0.130, 7 turns, 8.6 s |
| timeline_narrative | n/a | $0.124, 6.5 turns, 10.8 s | $0.141, 7.5 turns, 14.2 s |
| stance_change | n/a | $0.118, 6.5 turns, 10.0 s | $0.104, 5 turns, 9.9 s |
| open_loops | n/a | $0.137, 9 turns, 19.4 s | $0.142, 8 turns, 21.5 s |
| cross_channel_synthesis | n/a | $0.121, 7 turns, 17.6 s | $0.121, 7 turns, 15.8 s |
| entity_resolution | n/a | $0.090, 7.5 turns, 11.6 s | $0.114, 8 turns, 13.8 s |
| paraphrase | n/a | $0.097, 7 turns, 14.2 s | $0.136, 7.5 turns, 12.2 s |
| abstention | n/a | $0.088, 7 turns, 14.1 s | $0.080, 6 turns, 15.6 s |
| canary | n/a | $0.049, 1 turns, 2.5 s | $0.049, 1 turns, 2.4 s |
| Overall | n/a | $0.098, 7 turns, 12.4 s | $0.109, 6 turns, 11.9 s |
