# Eval comparison

- files-alone: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | files-alone |
|---|---|
| single_fact_recall | 0.83 (0.75 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 1.00 (1.00 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.83 (0.67 to 1.00) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 0.75 (0.75 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 0.89 (0.67 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 1.00 (1.00 to 1.00) |
| where_things_stand | 0.53 (0.40 to 0.60) |
| Overall | 0.88 (0.87 to 0.91) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-alone |
|---|---|
| single_fact_recall | $0.079, 3 turns, 10.1 s |
| attribution | $0.076, 3 turns, 9.9 s |
| timeline_narrative | $0.116, 4 turns, 15.8 s |
| stance_change | $0.085, 3 turns, 10.8 s |
| open_loops | $0.165, 7.5 turns, 29.5 s |
| cross_channel_synthesis | $0.106, 5 turns, 20.9 s |
| entity_resolution | $0.080, 3.5 turns, 11.7 s |
| paraphrase | $0.091, 5 turns, 12.3 s |
| abstention | $0.089, 6 turns, 15.1 s |
| canary | $0.047, 1 turns, 2.9 s |
| connection | $0.089, 3 turns, 12.8 s |
| where_things_stand | $0.095, 4 turns, 15.6 s |
| Overall | $0.090, 4 turns, 13.2 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-alone |
|---|---|
| single_fact_recall | 0.97 |
| attribution | 1.00 |
| timeline_narrative | 0.99 |
| stance_change | 1.00 |
| open_loops | 0.98 |
| cross_channel_synthesis | 0.94 |
| entity_resolution | 0.69 |
| paraphrase | 1.00 |
| abstention | 1.00 |
| canary | n/a |
| connection | 0.94 |
| where_things_stand | 0.99 |
| Overall | 0.96 |
