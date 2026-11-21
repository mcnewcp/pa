# Eval comparison

- files-plus-wiki: 3 runs (wiki librarian version 665463df6ee7)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 1.00 (1.00 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 1.00 (1.00 to 1.00) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 1.00 (1.00 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| Overall | 1.00 (1.00 to 1.00) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | $0.084, 4 turns, 7.4 s |
| attribution | $0.065, 4 turns, 7.0 s |
| timeline_narrative | $0.091, 5 turns, 12.3 s |
| stance_change | $0.071, 4 turns, 8.6 s |
| open_loops | $0.092, 6 turns, 14.4 s |
| cross_channel_synthesis | $0.118, 7 turns, 16.0 s |
| entity_resolution | $0.090, 7 turns, 10.2 s |
| paraphrase | $0.072, 4 turns, 8.2 s |
| abstention | $0.065, 4.5 turns, 8.7 s |
| canary | $0.030, 1 turns, 1.0 s |
| Overall | $0.084, 5 turns, 8.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 0.92 |
| timeline_narrative | 1.00 |
| stance_change | 1.00 |
| open_loops | 0.96 |
| cross_channel_synthesis | 0.83 |
| entity_resolution | 0.71 |
| paraphrase | 0.93 |
| abstention | 1.00 |
| canary | n/a |
| Overall | 0.92 |
