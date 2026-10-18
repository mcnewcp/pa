# Eval comparison

- scale-query-only-sonnet: 3 runs (configuration query-only)
- scale-query-only-opus: 3 runs (configuration query-only)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from scale-query-only-sonnet counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | scale-query-only-sonnet | scale-query-only-opus | scale-query-only-opus vs scale-query-only-sonnet |
|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 0.80 (0.80 to 0.80) | 0.93 (0.80 to 1.00) | +0.13 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 0.73 (0.60 to 0.80) | 0.67 (0.60 to 0.80) | -0.07 |
| cross_channel_synthesis | 0.78 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | +0.22 |
| entity_resolution | 0.75 (0.75 to 0.75) | 0.83 (0.75 to 1.00) | +0.08 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| connection | 0.67 (0.67 to 0.67) | 0.67 (0.67 to 0.67) | +0.00 |
| Overall | 0.88 (0.88 to 0.88) | 0.91 (0.90 to 0.93) | +0.03 (counts) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | scale-query-only-sonnet | scale-query-only-opus |
|---|---|---|
| single_fact_recall | $0.089, 7 turns, 11.3 s | $0.086, 7 turns, 11.6 s |
| attribution | $0.089, 7 turns, 11.2 s | $0.089, 7 turns, 10.5 s |
| timeline_narrative | $0.160, 9 turns, 19.6 s | $0.158, 9 turns, 18.3 s |
| stance_change | $0.114, 7 turns, 11.7 s | $0.113, 7 turns, 11.0 s |
| open_loops | $0.165, 9 turns, 17.4 s | $0.137, 8 turns, 21.2 s |
| cross_channel_synthesis | $0.140, 10 turns, 18.5 s | $0.146, 10 turns, 19.3 s |
| entity_resolution | $0.106, 9 turns, 15.8 s | $0.104, 8 turns, 12.6 s |
| paraphrase | $0.101, 8 turns, 13.0 s | $0.095, 8 turns, 14.4 s |
| abstention | $0.102, 9 turns, 18.7 s | $0.103, 9 turns, 17.2 s |
| canary | $0.050, 1 turns, 2.7 s | $0.049, 1 turns, 3.0 s |
| connection | $0.134, 9 turns, 15.6 s | $0.130, 9 turns, 15.4 s |
| Overall | $0.110, 8 turns, 14.0 s | $0.113, 8 turns, 14.9 s |
