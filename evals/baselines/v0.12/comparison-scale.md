# Eval comparison

- scale-files-alone: 3 runs (configuration files-alone)
- scale-graph-plus-files-sonnet: 3 runs (configuration graph-plus-files)
- scale-query-only-sonnet: 3 runs (configuration query-only)
- scale-graph-plus-files-opus: 3 runs (configuration graph-plus-files)
- scale-query-only-opus: 3 runs (configuration query-only)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from scale-files-alone counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | scale-files-alone | scale-graph-plus-files-sonnet | scale-query-only-sonnet | scale-graph-plus-files-opus | scale-query-only-opus | scale-graph-plus-files-sonnet vs scale-files-alone | scale-query-only-sonnet vs scale-files-alone | scale-graph-plus-files-opus vs scale-files-alone | scale-query-only-opus vs scale-files-alone |
|---|---|---|---|---|---|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 0.80 (0.80 to 0.80) | 0.80 (0.80 to 0.80) | 0.93 (0.80 to 1.00) | 0.93 (0.80 to 1.00) | -0.20 (counts) | -0.20 (counts) | -0.07 | -0.07 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| open_loops | 0.87 (0.80 to 1.00) | 0.80 (0.80 to 0.80) | 0.73 (0.60 to 0.80) | 0.80 (0.80 to 0.80) | 0.67 (0.60 to 0.80) | -0.07 | -0.13 | -0.07 | -0.20 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 0.89 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | -0.11 | -0.22 | -0.22 | +0.00 |
| entity_resolution | 0.75 (0.75 to 0.75) | 0.67 (0.50 to 0.75) | 0.75 (0.75 to 0.75) | 0.75 (0.75 to 0.75) | 0.83 (0.75 to 1.00) | -0.08 | +0.00 | +0.00 | +0.08 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 | +0.00 |
| connection | 0.78 (0.67 to 1.00) | 0.78 (0.67 to 1.00) | 0.67 (0.67 to 0.67) | 0.44 (0.33 to 0.67) | 0.67 (0.67 to 0.67) | +0.00 | -0.11 | -0.33 | -0.11 |
| Overall | 0.94 (0.93 to 0.95) | 0.89 (0.88 to 0.93) | 0.88 (0.88 to 0.88) | 0.88 (0.88 to 0.90) | 0.91 (0.90 to 0.93) | -0.05 (counts) | -0.07 (counts) | -0.06 (counts) | -0.03 (counts) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | scale-files-alone | scale-graph-plus-files-sonnet | scale-query-only-sonnet | scale-graph-plus-files-opus | scale-query-only-opus |
|---|---|---|---|---|---|
| single_fact_recall | $0.080, 5 turns, 9.5 s | $0.085, 6 turns, 9.5 s | $0.089, 7 turns, 11.3 s | $0.086, 5.5 turns, 12.3 s | $0.086, 7 turns, 11.6 s |
| attribution | $0.078, 5 turns, 9.2 s | $0.088, 5 turns, 9.9 s | $0.089, 7 turns, 11.2 s | $0.087, 5 turns, 10.5 s | $0.089, 7 turns, 10.5 s |
| timeline_narrative | $0.123, 6 turns, 16.8 s | $0.192, 9 turns, 16.5 s | $0.160, 9 turns, 19.6 s | $0.189, 9 turns, 19.6 s | $0.158, 9 turns, 18.3 s |
| stance_change | $0.077, 3.5 turns, 9.5 s | $0.116, 5 turns, 10.6 s | $0.114, 7 turns, 11.7 s | $0.123, 5.5 turns, 10.5 s | $0.113, 7 turns, 11.0 s |
| open_loops | $0.152, 10 turns, 26.7 s | $0.198, 10 turns, 19.4 s | $0.165, 9 turns, 17.4 s | $0.179, 9 turns, 21.5 s | $0.137, 8 turns, 21.2 s |
| cross_channel_synthesis | $0.101, 5 turns, 13.6 s | $0.151, 8 turns, 15.0 s | $0.140, 10 turns, 18.5 s | $0.172, 8 turns, 16.5 s | $0.146, 10 turns, 19.3 s |
| entity_resolution | $0.088, 5.5 turns, 11.0 s | $0.094, 7.5 turns, 11.2 s | $0.106, 9 turns, 15.8 s | $0.091, 7.5 turns, 12.9 s | $0.104, 8 turns, 12.6 s |
| paraphrase | $0.090, 6.5 turns, 12.9 s | $0.128, 7.5 turns, 12.5 s | $0.101, 8 turns, 13.0 s | $0.112, 7 turns, 13.1 s | $0.095, 8 turns, 14.4 s |
| abstention | $0.098, 7 turns, 15.8 s | $0.107, 9 turns, 20.7 s | $0.102, 9 turns, 18.7 s | $0.110, 8 turns, 20.9 s | $0.103, 9 turns, 17.2 s |
| canary | $0.048, 1 turns, 2.5 s | $0.049, 1 turns, 2.5 s | $0.050, 1 turns, 2.7 s | $0.050, 1 turns, 2.5 s | $0.049, 1 turns, 3.0 s |
| connection | $0.115, 7 turns, 16.7 s | $0.126, 8 turns, 14.0 s | $0.134, 9 turns, 15.6 s | $0.129, 8 turns, 15.4 s | $0.130, 9 turns, 15.4 s |
| Overall | $0.094, 5 turns, 11.7 s | $0.120, 7 turns, 13.1 s | $0.110, 8 turns, 14.0 s | $0.115, 7 turns, 14.6 s | $0.113, 8 turns, 14.9 s |
