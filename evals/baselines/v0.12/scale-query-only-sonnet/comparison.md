# Eval comparison

- query-only: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | query-only |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 0.80 (0.80 to 0.80) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.73 (0.60 to 0.80) |
| cross_channel_synthesis | 0.78 (0.67 to 1.00) |
| entity_resolution | 0.75 (0.75 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.67 (0.67 to 0.67) |
| Overall | 0.88 (0.88 to 0.88) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | query-only |
|---|---|
| single_fact_recall | $0.089, 7 turns, 11.3 s |
| attribution | $0.089, 7 turns, 11.2 s |
| timeline_narrative | $0.160, 9 turns, 19.6 s |
| stance_change | $0.114, 7 turns, 11.7 s |
| open_loops | $0.165, 9 turns, 17.4 s |
| cross_channel_synthesis | $0.140, 10 turns, 18.5 s |
| entity_resolution | $0.106, 9 turns, 15.8 s |
| paraphrase | $0.101, 8 turns, 13.0 s |
| abstention | $0.102, 9 turns, 18.7 s |
| canary | $0.050, 1 turns, 2.7 s |
| connection | $0.134, 9 turns, 15.6 s |
| Overall | $0.110, 8 turns, 14.0 s |
