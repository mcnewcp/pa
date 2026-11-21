# Eval comparison

- files-plus-wiki: 3 runs (wiki librarian version 8ea220fa48b4, dreamed by librarian version 8ea220fa48b4)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) |
| attribution | 1.00 (1.00 to 1.00) |
| timeline_narrative | 1.00 (1.00 to 1.00) |
| stance_change | 1.00 (1.00 to 1.00) |
| open_loops | 1.00 (1.00 to 1.00) |
| cross_channel_synthesis | 0.78 (0.67 to 1.00) |
| entity_resolution | 0.83 (0.50 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| Overall | 0.96 (0.96 to 0.96) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | $0.062, 3 turns, 6.7 s |
| attribution | $0.063, 4 turns, 6.7 s |
| timeline_narrative | $0.082, 4.5 turns, 12.0 s |
| stance_change | $0.091, 4.5 turns, 8.7 s |
| open_loops | $0.098, 6 turns, 14.5 s |
| cross_channel_synthesis | $0.105, 6 turns, 15.1 s |
| entity_resolution | $0.072, 5 turns, 9.9 s |
| paraphrase | $0.068, 4.5 turns, 8.6 s |
| abstention | $0.065, 5 turns, 8.8 s |
| canary | $0.029, 1 turns, 1.0 s |
| Overall | $0.075, 4 turns, 8.7 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 1.00 |
| timeline_narrative | 1.00 |
| stance_change | 0.73 |
| open_loops | 0.96 |
| cross_channel_synthesis | 0.85 |
| entity_resolution | 0.41 |
| paraphrase | 0.91 |
| abstention | 0.86 |
| canary | n/a |
| Overall | 0.87 |
