# Eval comparison

- scale-graph-plus-files-sonnet: 3 runs (configuration graph-plus-files)
- scale-graph-plus-files-opus: 3 runs (configuration graph-plus-files)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from scale-graph-plus-files-sonnet counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | scale-graph-plus-files-sonnet | scale-graph-plus-files-opus | scale-graph-plus-files-opus vs scale-graph-plus-files-sonnet |
|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 0.80 (0.80 to 0.80) | 0.93 (0.80 to 1.00) | +0.13 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 0.80 (0.80 to 0.80) | 0.80 (0.80 to 0.80) | +0.00 |
| cross_channel_synthesis | 0.89 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | -0.11 |
| entity_resolution | 0.67 (0.50 to 0.75) | 0.75 (0.75 to 0.75) | +0.08 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| connection | 0.78 (0.67 to 1.00) | 0.44 (0.33 to 0.67) | -0.33 |
| Overall | 0.89 (0.88 to 0.93) | 0.88 (0.88 to 0.90) | -0.01 |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | scale-graph-plus-files-sonnet | scale-graph-plus-files-opus |
|---|---|---|
| single_fact_recall | $0.085, 6 turns, 9.5 s | $0.086, 5.5 turns, 12.3 s |
| attribution | $0.088, 5 turns, 9.9 s | $0.087, 5 turns, 10.5 s |
| timeline_narrative | $0.192, 9 turns, 16.5 s | $0.189, 9 turns, 19.6 s |
| stance_change | $0.116, 5 turns, 10.6 s | $0.123, 5.5 turns, 10.5 s |
| open_loops | $0.198, 10 turns, 19.4 s | $0.179, 9 turns, 21.5 s |
| cross_channel_synthesis | $0.151, 8 turns, 15.0 s | $0.172, 8 turns, 16.5 s |
| entity_resolution | $0.094, 7.5 turns, 11.2 s | $0.091, 7.5 turns, 12.9 s |
| paraphrase | $0.128, 7.5 turns, 12.5 s | $0.112, 7 turns, 13.1 s |
| abstention | $0.107, 9 turns, 20.7 s | $0.110, 8 turns, 20.9 s |
| canary | $0.049, 1 turns, 2.5 s | $0.050, 1 turns, 2.5 s |
| connection | $0.126, 8 turns, 14.0 s | $0.129, 8 turns, 15.4 s |
| Overall | $0.120, 7 turns, 13.1 s | $0.115, 7 turns, 14.6 s |
