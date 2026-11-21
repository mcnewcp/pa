# Eval comparison

- argus-v0.12.1-wiki: 3 runs (configuration files-plus-wiki; wiki librarian version 665463df6ee7)
- argus-replayed: 3 runs (configuration files-plus-wiki; wiki librarian version 8ea220fa48b4)
- argus-dreamed: 3 runs (configuration files-plus-wiki; wiki librarian version 8ea220fa48b4, dreamed by librarian version 8ea220fa48b4)
- argus-dreamed-again: 3 runs (configuration files-plus-wiki; wiki librarian version 8ea220fa48b4, dreamed by librarian version 8ea220fa48b4)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from argus-v0.12.1-wiki counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | argus-v0.12.1-wiki | argus-replayed | argus-dreamed | argus-dreamed-again | argus-replayed vs argus-v0.12.1-wiki | argus-dreamed vs argus-v0.12.1-wiki | argus-dreamed-again vs argus-v0.12.1-wiki |
|---|---|---|---|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| open_loops | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.78 (0.67 to 1.00) | 0.89 (0.67 to 1.00) | +0.00 | -0.22 | -0.11 |
| entity_resolution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.83 (0.50 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | -0.17 | +0.00 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 | +0.00 | +0.00 |
| Overall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | 0.96 (0.96 to 0.96) | 0.99 (0.96 to 1.00) | +0.00 | -0.04 (counts) | -0.01 |

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | argus-v0.12.1-wiki | argus-replayed | argus-dreamed | argus-dreamed-again |
|---|---|---|---|---|
| single_fact_recall | $0.084, 4 turns, 7.4 s | $0.080, 4 turns, 8.2 s | $0.062, 3 turns, 6.7 s | $0.077, 4 turns, 7.9 s |
| attribution | $0.065, 4 turns, 7.0 s | $0.061, 4 turns, 6.3 s | $0.063, 4 turns, 6.7 s | $0.061, 4 turns, 6.7 s |
| timeline_narrative | $0.091, 5 turns, 12.3 s | $0.088, 4.5 turns, 10.8 s | $0.082, 4.5 turns, 12.0 s | $0.103, 7 turns, 11.3 s |
| stance_change | $0.071, 4 turns, 8.6 s | $0.081, 4.5 turns, 9.6 s | $0.091, 4.5 turns, 8.7 s | $0.073, 4 turns, 8.9 s |
| open_loops | $0.092, 6 turns, 14.4 s | $0.101, 7 turns, 13.7 s | $0.098, 6 turns, 14.5 s | $0.102, 6 turns, 15.3 s |
| cross_channel_synthesis | $0.118, 7 turns, 16.0 s | $0.109, 7 turns, 13.9 s | $0.105, 6 turns, 15.1 s | $0.104, 7 turns, 15.1 s |
| entity_resolution | $0.090, 7 turns, 10.2 s | $0.083, 5 turns, 9.7 s | $0.072, 5 turns, 9.9 s | $0.084, 6.5 turns, 10.1 s |
| paraphrase | $0.072, 4 turns, 8.2 s | $0.075, 5 turns, 9.6 s | $0.068, 4.5 turns, 8.6 s | $0.072, 4 turns, 8.7 s |
| abstention | $0.065, 4.5 turns, 8.7 s | $0.058, 4 turns, 7.7 s | $0.065, 5 turns, 8.8 s | $0.056, 4 turns, 7.5 s |
| canary | $0.030, 1 turns, 1.0 s | $0.029, 1 turns, 1.1 s | $0.029, 1 turns, 1.0 s | $0.032, 1.5 turns, 1.8 s |
| Overall | $0.084, 5 turns, 8.8 s | $0.080, 5 turns, 9.0 s | $0.075, 4 turns, 8.7 s | $0.082, 4 turns, 8.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | argus-v0.12.1-wiki | argus-replayed | argus-dreamed | argus-dreamed-again |
|---|---|---|---|---|
| single_fact_recall | 1.00 | 1.00 | 1.00 | 1.00 |
| attribution | 0.92 | 1.00 | 1.00 | 1.00 |
| timeline_narrative | 1.00 | 1.00 | 1.00 | 1.00 |
| stance_change | 1.00 | 0.93 | 0.73 | 0.82 |
| open_loops | 0.96 | 1.00 | 0.96 | 0.88 |
| cross_channel_synthesis | 0.83 | 0.72 | 0.85 | 0.83 |
| entity_resolution | 0.71 | 0.87 | 0.41 | 0.77 |
| paraphrase | 0.93 | 0.88 | 0.91 | 0.88 |
| abstention | 1.00 | 0.88 | 0.86 | 0.88 |
| canary | n/a | n/a | n/a | n/a |
| Overall | 0.92 | 0.90 | 0.87 | 0.89 |
