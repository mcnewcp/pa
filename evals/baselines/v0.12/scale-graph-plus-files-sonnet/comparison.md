# Eval comparison

- graph-plus-files: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | graph-plus-files |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 0.80 (0.80 to 0.80) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.80 (0.80 to 0.80) |
| cross_channel_synthesis | 0.89 (0.67 to 1.00) |
| entity_resolution | 0.67 (0.50 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.78 (0.67 to 1.00) |
| Overall | 0.89 (0.88 to 0.93) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | graph-plus-files |
|---|---|
| single_fact_recall | $0.085, 6 turns, 9.5 s |
| attribution | $0.088, 5 turns, 9.9 s |
| timeline_narrative | $0.192, 9 turns, 16.5 s |
| stance_change | $0.116, 5 turns, 10.6 s |
| open_loops | $0.198, 10 turns, 19.4 s |
| cross_channel_synthesis | $0.151, 8 turns, 15.0 s |
| entity_resolution | $0.094, 7.5 turns, 11.2 s |
| paraphrase | $0.128, 7.5 turns, 12.5 s |
| abstention | $0.107, 9 turns, 20.7 s |
| canary | $0.049, 1 turns, 2.5 s |
| connection | $0.126, 8 turns, 14.0 s |
| Overall | $0.120, 7 turns, 13.1 s |
