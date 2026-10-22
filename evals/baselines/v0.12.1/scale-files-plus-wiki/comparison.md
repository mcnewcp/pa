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
| open_loops | 0.78 (0.67 to 0.83) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 1.00 (1.00 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.78 (0.67 to 1.00) |
| where_things_stand | 0.80 (0.80 to 0.80) |
| Overall | 0.93 (0.91 to 0.96) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | $0.079, 4 turns, 10.5 s |
| attribution | $0.079, 3 turns, 9.4 s |
| timeline_narrative | $0.125, 5 turns, 17.6 s |
| stance_change | $0.102, 4.5 turns, 12.4 s |
| open_loops | $0.149, 7 turns, 25.2 s |
| cross_channel_synthesis | $0.110, 5 turns, 20.2 s |
| entity_resolution | $0.099, 5 turns, 11.9 s |
| paraphrase | $0.091, 4.5 turns, 11.3 s |
| abstention | $0.103, 6 turns, 15.2 s |
| canary | $0.049, 1 turns, 2.6 s |
| connection | $0.113, 5 turns, 13.9 s |
| where_things_stand | $0.106, 4 turns, 15.6 s |
| Overall | $0.100, 5 turns, 13.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 1.00 |
| timeline_narrative | 0.95 |
| stance_change | 0.76 |
| open_loops | 0.96 |
| cross_channel_synthesis | 0.92 |
| entity_resolution | 0.64 |
| paraphrase | 0.97 |
| abstention | 0.91 |
| canary | n/a |
| connection | 0.94 |
| where_things_stand | 0.68 |
| Overall | 0.88 |
