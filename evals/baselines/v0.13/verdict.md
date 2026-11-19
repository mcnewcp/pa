# v0.13 verdict: the wiki on real data

Numbers only. The questions, answers and failure analysis are in the private verdict under the data root.

- **The cut:** taken 2026-11-18 20:27 UTC, 1616 episodes and the live wiki.
- **The set:** 26 questions, asked at the cut's time.
- **The runs:** files alone 3, files plus wiki 3 (librarian version 665463df6ee7).
- **The setup:**
  - Agent: claude
  - Agent model: sonnet
  - Judge: model
  - Judge model: sonnet
  - Commit: 4377b4857b177963f579a91e8452e2b77ecf2c81
  - As-of method: both

## Correctness by category

Mean over runs, with the spread from the lowest run to the highest. A difference counts only when it is larger than the spread of either side.

| Category (questions) | Files alone | Files plus wiki | Wiki vs files alone |
|---|---|---|---|
| canary (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| single_fact_recall (6) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| paraphrase (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| timeline_narrative (5) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| attribution (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| where_things_stand (2) | 1.00 (1.00 to 1.00) | 0.83 (0.50 to 1.00) | -0.17 |
| abstention (3) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| stance_change (3) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| cross_channel_synthesis (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| open_loops (1) | 0.33 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.67 |
| entity_resolution (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| connection (1) | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.00 |
| Overall (26) | 0.97 (0.96 to 1.00) | 0.99 (0.96 to 1.00) | +0.01 |

## Evidence, citations and cost

| | Files alone | Files plus wiki |
|---|---|---|
| Evidence recall | 0.94 (0.92 to 0.99) | 0.95 (0.94 to 0.95) |
| Citation validity | 1.00 (1.00 to 1.00) | 0.99 (0.99 to 1.00) |
| Cited episodes opened | 0.98 | 0.95 |
| Total cost | $6.40 | $6.04 |
| Per answer, median cost | $0.078 | $0.076 |
| Per answer, median turns | 4 | 4 |
| Failed answers (agent or judge) | 0 | 0 |

## Search

Evidence recall on the real-data eval set against the scale corpus's (v0.12.1), in the same configuration, and of the evidence the answers left uncited, how much never reached their sessions.

| | Real-data eval set | Scale corpus | Lower by more than the spread | Missed evidence never opened |
|---|---|---|---|---|
| Files alone | 0.94 (0.92 to 0.99) | 0.90 (0.88 to 0.93) | no | 3 of 8 (0.38) |
| Files plus wiki | 0.95 (0.94 to 0.95) | 0.92 (0.90 to 0.94) | no | 3 of 14 (0.21) |

**Not needed:** in neither configuration is recall lower than the scale corpus's by more than the spread while most of the missed evidence was never opened.

## The prototype

**Met.** Files plus wiki scores 0.99 overall against files alone's 0.97, over 3 and 3 runs. The phone demo, the criterion's other part, is the owner's to confirm.
