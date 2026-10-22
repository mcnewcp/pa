# Eval comparison

- files-alone: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | files-alone |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 1.00 (1.00 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.67 (0.67 to 0.67) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 0.75 (0.75 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 1.00 (1.00 to 1.00) |
| where_things_stand | 0.53 (0.40 to 0.60) |
| Overall | 0.88 (0.87 to 0.89) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-alone |
|---|---|
| single_fact_recall | $0.078, 3 turns, 8.9 s |
| attribution | $0.070, 3 turns, 8.5 s |
| timeline_narrative | $0.113, 3 turns, 13.1 s |
| stance_change | $0.085, 3 turns, 9.7 s |
| open_loops | $0.175, 8 turns, 29.7 s |
| cross_channel_synthesis | $0.105, 5 turns, 17.3 s |
| entity_resolution | $0.076, 3.5 turns, 10.3 s |
| paraphrase | $0.081, 4.5 turns, 10.7 s |
| abstention | $0.088, 5 turns, 13.5 s |
| canary | $0.047, 1 turns, 2.6 s |
| connection | $0.089, 3 turns, 11.3 s |
| where_things_stand | $0.109, 3 turns, 11.6 s |
| Overall | $0.089, 4 turns, 11.5 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose file the session read (n/a: none cite anything).

| Category | files-alone |
|---|---|
| single_fact_recall | 0.03 |
| attribution | 0.06 |
| timeline_narrative | 0.00 |
| stance_change | 0.00 |
| open_loops | 0.02 |
| cross_channel_synthesis | 0.10 |
| entity_resolution | 0.12 |
| paraphrase | 0.09 |
| abstention | 0.29 |
| canary | n/a |
| connection | 0.00 |
| where_things_stand | 0.00 |
| Overall | 0.04 |
