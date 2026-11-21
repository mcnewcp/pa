# Eval comparison

- files-plus-wiki: 3 runs (wiki librarian version 665463df6ee7, dreamed by librarian version 8ea220fa48b4)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 0.93 (0.80 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.83 (0.67 to 1.00) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 0.92 (0.75 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 1.00 (1.00 to 1.00) |
| where_things_stand | 0.87 (0.80 to 1.00) |
| Overall | 0.95 (0.93 to 0.96) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | $0.101, 5 turns, 8.7 s |
| attribution | $0.072, 4 turns, 7.2 s |
| timeline_narrative | $0.109, 5 turns, 14.2 s |
| stance_change | $0.102, 4.5 turns, 9.5 s |
| open_loops | $0.146, 8 turns, 21.4 s |
| cross_channel_synthesis | $0.118, 6 turns, 15.4 s |
| entity_resolution | $0.087, 5 turns, 11.1 s |
| paraphrase | $0.095, 5 turns, 10.5 s |
| abstention | $0.089, 6 turns, 12.1 s |
| canary | $0.029, 1 turns, 0.9 s |
| connection | $0.104, 6 turns, 10.7 s |
| where_things_stand | $0.127, 8 turns, 13.2 s |
| Overall | $0.103, 5 turns, 11.0 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 1.00 |
| timeline_narrative | 0.85 |
| stance_change | 0.87 |
| open_loops | 0.97 |
| cross_channel_synthesis | 0.83 |
| entity_resolution | 0.61 |
| paraphrase | 0.93 |
| abstention | 0.97 |
| canary | n/a |
| connection | 0.78 |
| where_things_stand | 0.80 |
| Overall | 0.86 |
