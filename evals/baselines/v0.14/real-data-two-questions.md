# Wiki against wiki on real data

Numbers only. The questions, answers and failure analysis are in the private analysis under the data root.

- **The cut:** taken 2026-11-18 20:27 UTC, 1616 episodes and the live wiki.
- **The set:** 2 questions, asked at the cut's time.
- **The runs:** files plus wiki, the undreamed wiki 8, the dreamed wiki 8.
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
| where_things_stand | 0.88 (0.00 to 1.00) | 0.75 (0.00 to 1.00) | 0.12 | 1.00 | yes |
| open_loops | 0.50 (0.00 to 1.00) | 0.50 (0.00 to 1.00) | none | 1.00 | yes |
| Overall | 0.69 (0.00 to 1.00) | 0.62 (0.00 to 1.00) | 0.06 | n/a | no |

**Fails**: overall correctness is 0.62, below 0.69.

## Evidence, citations and cost

| | The undreamed wiki | The dreamed wiki |
|---|---|---|
| Evidence recall | 0.74 (0.43 to 1.00) | 0.71 (0.36 to 1.00) |
| Citation validity | 0.99 (0.93 to 1.00) | 0.94 (0.50 to 1.00) |
| Cited episodes opened | 0.82 | 0.82 |
| Total cost | $2.10 | $1.98 |
| Per answer, median cost | $0.134 | $0.115 |
| Per answer, median turns | 6 | 6 |
| Failed answers (agent or judge) | 0 | 0 |
