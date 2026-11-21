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
| cross_channel_synthesis | 0.89 (0.67 to 1.00) |
| entity_resolution | 1.00 (1.00 to 1.00) |
| paraphrase | 1.00 (1.00 to 1.00) |
| abstention | 1.00 (1.00 to 1.00) |
| canary | 1.00 (1.00 to 1.00) |
| Overall | 0.99 (0.96 to 1.00) |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | $0.077, 4 turns, 7.9 s |
| attribution | $0.061, 4 turns, 6.7 s |
| timeline_narrative | $0.103, 7 turns, 11.3 s |
| stance_change | $0.073, 4 turns, 8.9 s |
| open_loops | $0.102, 6 turns, 15.3 s |
| cross_channel_synthesis | $0.104, 7 turns, 15.1 s |
| entity_resolution | $0.084, 6.5 turns, 10.1 s |
| paraphrase | $0.072, 4 turns, 8.7 s |
| abstention | $0.056, 4 turns, 7.5 s |
| canary | $0.032, 1.5 turns, 1.8 s |
| Overall | $0.082, 4 turns, 8.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | files-plus-wiki |
|---|---|
| single_fact_recall | 1.00 |
| attribution | 1.00 |
| timeline_narrative | 1.00 |
| stance_change | 0.82 |
| open_loops | 0.88 |
| cross_channel_synthesis | 0.83 |
| entity_resolution | 0.77 |
| paraphrase | 0.88 |
| abstention | 0.88 |
| canary | n/a |
| Overall | 0.89 |
