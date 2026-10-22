# Eval comparison

- scale-files-alone-rerun: 3 runs (configuration files-alone)
- scale-files-plus-wiki: 3 runs (configuration files-plus-wiki; wiki librarian version 665463df6ee7)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from scale-files-alone-rerun counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | scale-files-alone-rerun | scale-files-plus-wiki | scale-files-plus-wiki vs scale-files-alone-rerun |
|---|---|---|---|
| single_fact_recall | 0.83 (0.75 to 1.00) | 1.00 (1.00 to 1.00) | +0.17 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 0.83 (0.67 to 1.00) | 0.78 (0.67 to 0.83) | -0.06 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| entity_resolution | 0.75 (0.75 to 0.75) | 1.00 (1.00 to 1.00) | +0.25 (counts) |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 0.89 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | +0.11 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| connection | 1.00 (1.00 to 1.00) | 0.78 (0.67 to 1.00) | -0.22 |
| where_things_stand | 0.53 (0.40 to 0.60) | 0.80 (0.80 to 0.80) | +0.27 (counts) |
| Overall | 0.88 (0.87 to 0.91) | 0.93 (0.91 to 0.96) | +0.05 (counts) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | scale-files-alone-rerun | scale-files-plus-wiki |
|---|---|---|
| single_fact_recall | $0.079, 3 turns, 10.1 s | $0.079, 4 turns, 10.5 s |
| attribution | $0.076, 3 turns, 9.9 s | $0.079, 3 turns, 9.4 s |
| timeline_narrative | $0.116, 4 turns, 15.8 s | $0.125, 5 turns, 17.6 s |
| stance_change | $0.085, 3 turns, 10.8 s | $0.102, 4.5 turns, 12.4 s |
| open_loops | $0.165, 7.5 turns, 29.5 s | $0.149, 7 turns, 25.2 s |
| cross_channel_synthesis | $0.106, 5 turns, 20.9 s | $0.110, 5 turns, 20.2 s |
| entity_resolution | $0.080, 3.5 turns, 11.7 s | $0.099, 5 turns, 11.9 s |
| paraphrase | $0.091, 5 turns, 12.3 s | $0.091, 4.5 turns, 11.3 s |
| abstention | $0.089, 6 turns, 15.1 s | $0.103, 6 turns, 15.2 s |
| canary | $0.047, 1 turns, 2.9 s | $0.049, 1 turns, 2.6 s |
| connection | $0.089, 3 turns, 12.8 s | $0.113, 5 turns, 13.9 s |
| where_things_stand | $0.095, 4 turns, 15.6 s | $0.106, 4 turns, 15.6 s |
| Overall | $0.090, 4 turns, 13.2 s | $0.100, 5 turns, 13.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | scale-files-alone-rerun | scale-files-plus-wiki |
|---|---|---|
| single_fact_recall | 0.97 | 1.00 |
| attribution | 1.00 | 1.00 |
| timeline_narrative | 0.99 | 0.95 |
| stance_change | 1.00 | 0.76 |
| open_loops | 0.98 | 0.96 |
| cross_channel_synthesis | 0.94 | 0.92 |
| entity_resolution | 0.69 | 0.64 |
| paraphrase | 1.00 | 0.97 |
| abstention | 1.00 | 0.91 |
| canary | n/a | n/a |
| connection | 0.94 | 0.94 |
| where_things_stand | 0.99 | 0.68 |
| Overall | 0.96 | 0.88 |
