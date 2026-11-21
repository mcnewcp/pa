# Eval comparison

- files-plus-wiki: 3 runs (wiki librarian version 8ea220fa48b4)

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
| single_fact_recall | $0.080, 4 turns, 8.2 s |
| attribution | $0.061, 4 turns, 6.3 s |
| timeline_narrative | $0.088, 4.5 turns, 10.8 s |
| stance_change | $0.081, 4.5 turns, 9.6 s |
| open_loops | $0.101, 7 turns, 13.7 s |
| cross_channel_synthesis | $0.109, 7 turns, 13.9 s |
| entity_resolution | $0.083, 5 turns, 9.7 s |
| paraphrase | $0.075, 5 turns, 9.6 s |
| abstention | $0.058, 4 turns, 7.7 s |
| canary | $0.029, 1 turns, 1.1 s |
| Overall | $0.080, 5 turns, 9.0 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 1.00 |
| timeline_narrative | 1.00 |
| stance_change | 0.93 |
| open_loops | 1.00 |
| cross_channel_synthesis | 0.72 |
| entity_resolution | 0.87 |
| paraphrase | 0.88 |
| abstention | 0.88 |
| canary | n/a |
| Overall | 0.90 |
