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
| open_loops | 0.87 (0.80 to 1.00) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 0.75 (0.75 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.78 (0.67 to 1.00) |
| Overall | 0.94 (0.93 to 0.95) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-alone |
|---|---|
| single_fact_recall | $0.080, 5 turns, 9.5 s |
| attribution | $0.078, 5 turns, 9.2 s |
| timeline_narrative | $0.123, 6 turns, 16.8 s |
| stance_change | $0.077, 3.5 turns, 9.5 s |
| open_loops | $0.152, 10 turns, 26.7 s |
| cross_channel_synthesis | $0.101, 5 turns, 13.6 s |
| entity_resolution | $0.088, 5.5 turns, 11.0 s |
| paraphrase | $0.090, 6.5 turns, 12.9 s |
| abstention | $0.098, 7 turns, 15.8 s |
| canary | $0.048, 1 turns, 2.5 s |
| connection | $0.115, 7 turns, 16.7 s |
| Overall | $0.094, 5 turns, 11.7 s |
