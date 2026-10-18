# Eval comparison

- query-only: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | query-only |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 0.93 (0.80 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.67 (0.60 to 0.80) |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) |
| entity_resolution | 0.83 (0.75 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.67 (0.67 to 0.67) |
| Overall | 0.91 (0.90 to 0.93) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | query-only |
|---|---|
| single_fact_recall | $0.086, 7 turns, 11.6 s |
| attribution | $0.089, 7 turns, 10.5 s |
| timeline_narrative | $0.158, 9 turns, 18.3 s |
| stance_change | $0.113, 7 turns, 11.0 s |
| open_loops | $0.137, 8 turns, 21.2 s |
| cross_channel_synthesis | $0.146, 10 turns, 19.3 s |
| entity_resolution | $0.104, 8 turns, 12.6 s |
| paraphrase | $0.095, 8 turns, 14.4 s |
| abstention | $0.103, 9 turns, 17.2 s |
| canary | $0.049, 1 turns, 3.0 s |
| connection | $0.130, 9 turns, 15.4 s |
| Overall | $0.113, 8 turns, 14.9 s |
