# Eval comparison

- graph-plus-files: 3 runs

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | graph-plus-files |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 0.93 (0.80 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 0.80 (0.80 to 0.80) |
| cross_channel_synthesis | 0.78 (0.67 to 1.00) |
| entity_resolution | 0.75 (0.75 to 0.75) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| connection | 0.44 (0.33 to 0.67) |
| Overall | 0.88 (0.88 to 0.90) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | graph-plus-files |
|---|---|
| single_fact_recall | $0.086, 5.5 turns, 12.3 s |
| attribution | $0.087, 5 turns, 10.5 s |
| timeline_narrative | $0.189, 9 turns, 19.6 s |
| stance_change | $0.123, 5.5 turns, 10.5 s |
| open_loops | $0.179, 9 turns, 21.5 s |
| cross_channel_synthesis | $0.172, 8 turns, 16.5 s |
| entity_resolution | $0.091, 7.5 turns, 12.9 s |
| paraphrase | $0.112, 7 turns, 13.1 s |
| abstention | $0.110, 8 turns, 20.9 s |
| canary | $0.050, 1 turns, 2.5 s |
| connection | $0.129, 8 turns, 15.4 s |
| Overall | $0.115, 7 turns, 14.6 s |
