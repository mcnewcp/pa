# Eval comparison

- scale-undreamed: 3 runs (configuration files-plus-wiki; wiki librarian version 665463df6ee7)
- scale-dreamed: 3 runs (configuration files-plus-wiki; wiki librarian version 665463df6ee7, dreamed by librarian version 8ea220fa48b4)

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest.
A difference from scale-undreamed counts only when it is larger than the spread of either side, and is marked (counts); a single run has no spread, so it never counts.

| Category | scale-undreamed | scale-dreamed | scale-dreamed vs scale-undreamed |
|---|---|---|---|
| single_fact_recall | 0.92 (0.75 to 1.00) | 1.00 (1.00 to 1.00) | +0.08 |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative | 0.93 (0.80 to 1.00) | 0.93 (0.80 to 1.00) | +0.00 |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops | 0.89 (0.67 to 1.00) | 0.83 (0.67 to 1.00) | -0.06 |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| entity_resolution | 1.00 (1.00 to 1.00) | 0.92 (0.75 to 1.00) | -0.08 |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| connection | 0.78 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | +0.22 |
| where_things_stand | 0.73 (0.60 to 0.80) | 0.87 (0.80 to 1.00) | +0.13 |
| Overall | 0.93 (0.87 to 0.98) | 0.95 (0.93 to 0.96) | +0.02 |

## Wiki against wiki

Both sets of runs were given an eval wiki, and not the same one: this is scale-dreamed's against scale-undreamed's. It passes when its overall correctness is at least scale-undreamed's, and no category drops by more than the run-to-run spread. The spread is the one scale-undreamed's own runs show in that category, from the lowest to the highest: scale-dreamed's spread excuses nothing, and a single run has none, so any drop from it is too far.

| Category | scale-undreamed | scale-dreamed | Drop | Spread | Within it |
|---|---|---|---|---|---|
| single_fact_recall | 0.92 (0.75 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.25 | yes |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| timeline_narrative | 0.93 (0.80 to 1.00) | 0.93 (0.80 to 1.00) | none | 0.20 | yes |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| open_loops | 0.89 (0.67 to 1.00) | 0.83 (0.67 to 1.00) | 0.06 | 0.33 | yes |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| entity_resolution | 1.00 (1.00 to 1.00) | 0.92 (0.75 to 1.00) | 0.08 | 0.00 | no |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| connection | 0.78 (0.67 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.33 | yes |
| where_things_stand | 0.73 (0.60 to 0.80) | 0.87 (0.80 to 1.00) | none | 0.20 | yes |
| Overall | 0.93 (0.87 to 0.98) | 0.95 (0.93 to 0.96) | none | n/a | yes |

**Fails**: entity_resolution drops by 0.08, more than the spread of 0.00.

## Efficiency by category

Median cost, turns, and duration over every answer in every run that reports them (n/a: none do).

| Category | scale-undreamed | scale-dreamed |
|---|---|---|
| single_fact_recall | $0.097, 5 turns, 8.7 s | $0.101, 5 turns, 8.7 s |
| attribution | $0.076, 4 turns, 7.5 s | $0.072, 4 turns, 7.2 s |
| timeline_narrative | $0.113, 6 turns, 13.2 s | $0.109, 5 turns, 14.2 s |
| stance_change | $0.078, 4 turns, 9.0 s | $0.102, 4.5 turns, 9.5 s |
| open_loops | $0.160, 8 turns, 22.7 s | $0.146, 8 turns, 21.4 s |
| cross_channel_synthesis | $0.131, 7 turns, 16.0 s | $0.118, 6 turns, 15.4 s |
| entity_resolution | $0.093, 5 turns, 9.3 s | $0.087, 5 turns, 11.1 s |
| paraphrase | $0.089, 5 turns, 9.5 s | $0.095, 5 turns, 10.5 s |
| abstention | $0.079, 6 turns, 10.6 s | $0.089, 6 turns, 12.1 s |
| canary | $0.036, 2 turns, 3.0 s | $0.029, 1 turns, 0.9 s |
| connection | $0.095, 5 turns, 9.3 s | $0.104, 6 turns, 10.7 s |
| where_things_stand | $0.120, 7 turns, 13.5 s | $0.127, 8 turns, 13.2 s |
| Overall | $0.100, 5 turns, 10.7 s | $0.103, 5 turns, 11.0 s |

## Cited episodes opened

Of the episodes cited by every answer in every run whose tool calls were recorded, the share whose contents reached the session (n/a: none cite anything).

| Category | scale-undreamed | scale-dreamed |
|---|---|---|
| single_fact_recall | 1.00 | 1.00 |
| attribution | 0.98 | 1.00 |
| timeline_narrative | 0.81 | 0.85 |
| stance_change | 0.80 | 0.87 |
| open_loops | 0.94 | 0.97 |
| cross_channel_synthesis | 0.92 | 0.83 |
| entity_resolution | 0.64 | 0.61 |
| paraphrase | 0.98 | 0.93 |
| abstention | 0.97 | 0.97 |
| canary | n/a | n/a |
| connection | 0.90 | 0.78 |
| where_things_stand | 0.84 | 0.80 |
| Overall | 0.87 | 0.86 |
