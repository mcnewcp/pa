# Wiki against wiki on real data

Numbers only. The questions, answers and failure analysis are in the private analysis under the data root.

- **The cut:** taken 2026-11-18 20:27 UTC, 1616 episodes and the live wiki.
- **The set:** 53 questions, asked at the cut's time.
- **The runs:** files plus wiki, the undreamed wiki 6, the dreamed wiki 3.
- **The wikis:**
  - The undreamed wiki: librarian version 665463df6ee7
  - The dreamed wiki: librarian version 665463df6ee7, dreamed by librarian version 8ea220fa48b4
- **The setup:**
  - Agent: claude
  - Agent model: sonnet
  - Judge: model
  - Judge model: sonnet
  - Commit: 546ebc8518f99e67481f464d25901289106d060c
  - As-of method: both

## Wiki against wiki

Both sets of runs were given an eval wiki, and not the same one: this is the dreamed wiki's against the undreamed wiki's, over the 53 questions every run asked.

- **A question's score** on a wiki is the share of that wiki's runs that got it right.
- **The drop** is the mean over questions of the undreamed wiki's score minus the dreamed wiki's, so a rise is negative.
- **The noise floor** is the largest drop between any two halves of the undreamed wiki's own runs. A half is 3 runs, as many as the dreamed wiki was asked, and every pair of halves that share no run is tried, each way round: 10 pairs from its 6 runs.
- **It passes** when the drop is no larger than the noise floor, overall and in every tier.
- **(few)** marks fewer than 5 questions. Such a tier counts like any other, but one of the dreamed wiki's answers moves its drop by 0.083 or more, so its result says little by itself.

| | Questions | the undreamed wiki | the dreamed wiki | Drop | Noise floor | Within it |
|---|---|---|---|---|---|---|
| Overall | 53 | 0.96 | 0.97 | -0.006 | 0.025 | yes |
| Tier 0 | 6 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 1 | 6 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 2 | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 3 | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 4 | 5 | 0.90 | 1.00 | -0.100 | 0.200 | yes |
| Tier 5 | 7 | 0.81 | 0.81 | 0.000 | 0.095 | yes |
| Tier 6 | 5 | 0.97 | 1.00 | -0.033 | 0.067 | yes |
| Tier 7 | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 8 | 3 (few) | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 9 | 6 | 1.00 | 0.94 | 0.056 | 0.000 | no |

**Fails**: tier 9 drops by 0.056, more than the noise floor of 0.000.

### By category

The same drop and noise floor by category. The eval set has tiers, so its categories decide nothing.

| Category | Questions | the undreamed wiki | the dreamed wiki | Drop | Noise floor |
|---|---|---|---|---|---|
| canary | 1 | 1.00 | 1.00 | 0.000 | 0.000 |
| single_fact_recall | 15 | 1.00 | 1.00 | 0.000 | 0.000 |
| stance_change | 7 | 1.00 | 1.00 | 0.000 | 0.000 |
| entity_resolution | 1 | 1.00 | 1.00 | 0.000 | 0.000 |
| paraphrase | 6 | 1.00 | 1.00 | 0.000 | 0.000 |
| cross_channel_synthesis | 1 | 1.00 | 1.00 | 0.000 | 0.000 |
| where_things_stand | 4 | 0.96 | 1.00 | -0.042 | 0.083 |
| timeline_narrative | 6 | 0.92 | 0.94 | -0.028 | 0.167 |
| open_loops | 10 | 0.87 | 0.87 | 0.000 | 0.067 |
| connection | 1 | 1.00 | 1.00 | 0.000 | 0.000 |
| abstention | 1 | 1.00 | 1.00 | 0.000 | 0.000 |

## Evidence, citations and cost

| | The undreamed wiki | The dreamed wiki |
|---|---|---|
| Evidence recall | 0.97 (0.94 to 0.99) | 0.97 (0.96 to 0.98) |
| Citation validity | 0.99 (0.96 to 1.00) | 0.99 (0.97 to 1.00) |
| Cited episodes opened | 0.93 | 0.93 |
| Total cost | $31.21 | $15.18 |
| Per answer, median cost | $0.093 | $0.096 |
| Per answer, median turns | 5 | 5 |
| Failed answers (agent or judge) | 0 | 0 |
