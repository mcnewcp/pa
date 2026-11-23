# Wiki against wiki on real data

Numbers only. The questions, answers and failure analysis are in the private analysis under the data root.

- **The cut:** taken 2026-11-18 20:27 UTC, 1616 episodes and the live wiki.
- **The set:** 26 questions, asked at the cut's time.
- **The runs:** files plus wiki, the undreamed wiki 3, the dreamed wiki 3.
- **The wikis:**
  - The undreamed wiki: librarian version 665463df6ee7
  - The dreamed wiki: librarian version 665463df6ee7, dreamed by librarian version 8ea220fa48b4
- **The setup:**
  - Agent: claude
  - Agent model: sonnet
  - Judge: model
  - Judge model: sonnet
  - Commit: fe227b3dd15f7a65481082c8fa585e47f5deeea1
  - As-of method: both

## Wiki against wiki

Both sets of runs were given an eval wiki, and not the same one: this is the dreamed wiki's against the undreamed wiki's. It passes when its overall correctness is at least the undreamed wiki's, and no category drops by more than the run-to-run spread. The spread is the one the undreamed wiki's own runs show in that category, from the lowest to the highest: the dreamed wiki's spread excuses nothing, and a single run has none, so any drop from it is too far.

| Category | the undreamed wiki | the dreamed wiki | Drop | Spread | Within it |
|---|---|---|---|---|---|
| canary | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| single_fact_recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| paraphrase | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| timeline_narrative | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| attribution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| where_things_stand | 1.00 (1.00 to 1.00) | 0.83 (0.50 to 1.00) | 0.17 | 0.00 | no |
| abstention | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| stance_change | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| cross_channel_synthesis | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| open_loops | 0.67 (0.00 to 1.00) | 0.33 (0.00 to 1.00) | 0.33 | 1.00 | yes |
| entity_resolution | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| connection | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 0.00 | yes |
| Overall | 0.99 (0.96 to 1.00) | 0.96 (0.96 to 0.96) | 0.03 | n/a | no |

**Fails**: overall correctness is 0.96, below 0.99.
**Fails**: where_things_stand drops by 0.17, more than the spread of 0.00.

## Evidence, citations and cost

| | The undreamed wiki | The dreamed wiki |
|---|---|---|
| Evidence recall | 0.93 (0.89 to 0.96) | 0.91 (0.90 to 0.92) |
| Citation validity | 0.99 (0.98 to 1.00) | 0.99 (0.99 to 1.00) |
| Cited episodes opened | 0.97 | 0.93 |
| Total cost | $7.53 | $7.54 |
| Per answer, median cost | $0.097 | $0.097 |
| Per answer, median turns | 5 | 5 |
| Failed answers (agent or judge) | 0 | 0 |
