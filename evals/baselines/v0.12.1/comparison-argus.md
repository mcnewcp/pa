# Eval comparison

- argus-files-alone: 1 run (configuration files-alone)
- argus-files-plus-wiki: 1 run (configuration files-plus-wiki; wiki librarian version 665463df6ee7)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from argus-files-alone counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | argus-files-alone | argus-files-plus-wiki | argus-files-plus-wiki vs argus-files-alone |
|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| entity_resolution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| Overall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | argus-files-alone | argus-files-plus-wiki |
|---|---|---|
| single_fact_recall | $0.077, 3 turns, 7.3 s | $0.072, 3 turns, 8.1 s |
| attribution | $0.066, 3 turns, 8.4 s | $0.071, 3 turns, 9.4 s |
| timeline_narrative | $0.086, 3 turns, 10.8 s | $0.104, 4 turns, 12.6 s |
| stance_change | $0.073, 3 turns, 10.0 s | $0.092, 4 turns, 14.0 s |
| open_loops | $0.118, 4 turns, 17.0 s | $0.121, 6 turns, 21.3 s |
| cross_channel_synthesis | $0.087, 4 turns, 14.0 s | $0.129, 6 turns, 20.5 s |
| entity_resolution | $0.085, 4.5 turns, 14.3 s | $0.081, 4 turns, 11.2 s |
| paraphrase | $0.081, 4 turns, 11.0 s | $0.087, 5 turns, 12.3 s |
| abstention | $0.077, 5 turns, 12.2 s | $0.085, 5.5 turns, 14.1 s |
| canary | $0.046, 1 turns, 2.8 s | $0.049, 1 turns, 3.5 s |
| Overall | $0.078, 3 turns, 11.1 s | $0.084, 4 turns, 11.7 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | argus-files-alone | argus-files-plus-wiki |
|---|---|---|
| single_fact_recall | 1.00 | 1.00 |
| attribution | 1.00 | 1.00 |
| timeline_narrative | 1.00 | 1.00 |
| stance_change | 1.00 | 1.00 |
| open_loops | 1.00 | 1.00 |
| cross_channel_synthesis | 1.00 | 1.00 |
| entity_resolution | 1.00 | 1.00 |
| paraphrase | 1.00 | 0.93 |
| abstention | 1.00 | 1.00 |
| canary | n/a | n/a |
| Overall | 1.00 | 0.99 |
