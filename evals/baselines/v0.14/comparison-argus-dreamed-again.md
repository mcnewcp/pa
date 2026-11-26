# Eval comparison

- argus-replayed: 3 runs (configuration files-plus-wiki; wiki librarian version 8ea220fa48b4)
- argus-dreamed-again: 3 runs (configuration files-plus-wiki; wiki librarian version 8ea220fa48b4, dreamed by librarian version 8ea220fa48b4)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from argus-replayed counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | argus-replayed | argus-dreamed-again | argus-dreamed-again vs argus-replayed |
|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 0.89 (0.67 to 1.00) | -0.11 |
| entity_resolution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| Overall | 1.00 (1.00 to 1.00) | 0.99 (0.96 to 1.00) | -0.01 |

## Wiki against wiki

Both sets of runs were given an eval wiki, and not the same one: this is argus-dreamed-again's against argus-replayed's. It passes when its overall correctness is at least argus-replayed's, and no category drops by more than the run-to-run spread. The spread is the one argus-replayed's own runs show in that category, from the lowest to the highest: argus-dreamed-again's spread excuses nothing, and a single run has none, so any drop from it is too far.

| Category | argus-replayed | argus-dreamed-again | Drop | Spread | Within it |
|---|---|---|---|---|---|
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| open_loops | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 0.89 (0.67 to 1.00) | 0.11 | 0.00 | no |
| entity_resolution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| Overall | 1.00 (1.00 to 1.00) | 0.99 (0.96 to 1.00) | 0.01 | n/a | no |

**Fails**: overall correctness is 0.99, below 1.00.
**Fails**: cross_channel_synthesis drops by 0.11, more than the spread of 0.00.

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | argus-replayed | argus-dreamed-again |
|---|---|---|
| single_fact_recall | $0.080, 4 turns, 8.2 s | $0.077, 4 turns, 7.9 s |
| attribution | $0.061, 4 turns, 6.3 s | $0.061, 4 turns, 6.7 s |
| timeline_narrative | $0.088, 4.5 turns, 10.8 s | $0.103, 7 turns, 11.3 s |
| stance_change | $0.081, 4.5 turns, 9.6 s | $0.073, 4 turns, 8.9 s |
| open_loops | $0.101, 7 turns, 13.7 s | $0.102, 6 turns, 15.3 s |
| cross_channel_synthesis | $0.109, 7 turns, 13.9 s | $0.104, 7 turns, 15.1 s |
| entity_resolution | $0.083, 5 turns, 9.7 s | $0.084, 6.5 turns, 10.1 s |
| paraphrase | $0.075, 5 turns, 9.6 s | $0.072, 4 turns, 8.7 s |
| abstention | $0.058, 4 turns, 7.7 s | $0.056, 4 turns, 7.5 s |
| canary | $0.029, 1 turns, 1.1 s | $0.032, 1.5 turns, 1.8 s |
| Overall | $0.080, 5 turns, 9.0 s | $0.082, 4 turns, 8.8 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | argus-replayed | argus-dreamed-again |
|---|---|---|
| single_fact_recall | 1.00 | 1.00 |
| attribution | 1.00 | 1.00 |
| timeline_narrative | 1.00 | 1.00 |
| stance_change | 0.93 | 0.82 |
| open_loops | 1.00 | 0.88 |
| cross_channel_synthesis | 0.72 | 0.83 |
| entity_resolution | 0.87 | 0.77 |
| paraphrase | 0.88 | 0.88 |
| abstention | 0.88 | 0.88 |
| canary | n/a | n/a |
| Overall | 0.90 | 0.89 |
