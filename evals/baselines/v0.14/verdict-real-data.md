# v0.14 on real data: the dreamed copy of the v0.13 cut's wiki against the cut's own

Numbers only. The questions, answers and failure analysis are in the private analyses under the data root, for the owner. This file was written from counts, scores and exit codes; no page, proposal or answer was read to write it.

#152 has been compared twice.

- **The graded comparison, by #156's rule, fails in one tier by one answer.** Over 53 questions in tiers 0 to 9, the dreamed copy scores 0.97 against 0.96 overall, inside the noise floor. Tier 9 fails: one wrong answer of 18, against a floor of zero. One tier-5 question the rule does not flag is wrong in every run on the copy and right in every run on the cut's wiki, and the counts tie it to a page dreaming removed.
- **The first comparison, by #140's criterion on the 26-question v0.13 set, failed** on three unstable questions: 0.95 against 0.99. It could not tell the wikis apart, which is why #156 replaced the set and the rule.

## The dreaming run

One run, in the librarian's VM, at the cut's as-of time (2026-11-18 20:27 UTC), by librarian version `8ea220fa48b4` with Claude Opus 5.5 at medium effort. The cut's wiki was built by `665463df6ee7`.

| | |
|---|---|
| Candidates judged | 29 |
| Applied | 6 |
| Proposed | 2, as 2 open proposals |
| Dismissed | 21 |
| Left over the cap | 0 |
| Cost | $2.64 |
| Turns | 77 |
| Resumes | 1 |
| Lint warnings after the run | 6 |

| Candidate kind | Offered | Applied | Proposed | Dismissed |
|---|---|---|---|---|
| stale | 3 | 2 | 0 | 1 |
| loop-past-due | 5 | 0 | 0 | 5 |
| loop-aging | 1 | 0 | 0 | 1 |
| unengaged | 20 | 4 | 2 | 14 |

- **What changed in the copy:** 5 pages changed and 4 pages gone, of 44. Three of the 4 were retired and are listed in `retired.md`; the fourth was merged into another page. People's pages went from 14 to 12, organizations' from 18 to 16, and the 12 topics and 132 day logs are all still there.
- **A second run** found nothing to judge: all 23 candidates left are held back.

## The graded comparison

#152's criterion, as #156 set it:

> Run files plus wiki over the cut 6 times with the cut's own wiki and 3 times with the dreamed copy, at the same commit, asking #156's graded set. It passes when the dreamed copy's drop is no larger than the noise floor, overall and in every tier.

All 9 runs ran in `pa-home` at commit `546ebc8` on 2026-11-23 and 24, with Sonnet answering and judging, over the same cut and the same dreamed copy as before. No question failed to run. The report is `real-data-graded.md`.

| Set | Runs | Correctness, run by run | Wrong answers | Cost |
|---|---|---|---|---|
| The cut's own wiki | 6 | 0.92, 0.96, 0.98, 0.96, 0.96, 0.98 | 12 of 318 | $31.21 |
| The dreamed copy | 3 | 0.96, 0.96, 0.98 | 5 of 159 | $15.18 |

| | Questions | The cut's own wiki | The dreamed copy | Drop | Noise floor | Within it |
|---|---|---|---|---|---|---|
| Overall | 53 | 0.96 | 0.97 | -0.006 | 0.025 | yes |
| Tiers 0 to 3 | 22 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 4 | 5 | 0.90 | 1.00 | -0.100 | 0.200 | yes |
| Tier 5 | 7 | 0.81 | 0.81 | 0.000 | 0.095 | yes |
| Tier 6 | 5 | 0.97 | 1.00 | -0.033 | 0.067 | yes |
| Tier 7 | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 8 | 3 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 9 | 6 | 1.00 | 0.94 | 0.056 | 0.000 | no |

- **It fails on tier 9 alone,** by one answer. The cut's wiki got all 36 of its tier-9 answers right, so that tier's noise floor is zero and a single miss on the copy is over it.
- **Evidence recall** is 0.97 on both wikis, and **citation validity** 0.99 on both.
- **Seven questions account for all 17 wrong answers.** The other 46 were right in all 9 runs.

| Question | The cut's own wiki, 6 runs | The dreamed copy, 3 runs |
|---|---|---|
| A tier-4 sweep | 2 wrong | 0 wrong |
| Another tier-4 sweep | 1 wrong | 0 wrong |
| The v0.13 open-loops question, kept in tier 5 | 2 wrong | 0 wrong |
| A tier-5 sweep | 6 wrong | 1 wrong |
| A tier-5 sweep, one of the two questions written on the merge | 0 wrong | 3 wrong |
| A tier-6 question on a page dreaming brought current | 1 wrong | 0 wrong |
| A tier-9 question | 0 wrong | 1 wrong |

### What the counts say

Each of these is a count over the run files and the two wikis, made without printing an answer or a page.

- **The tier-9 miss reads as noise.** Its session opened all 9 evidence episodes and cited 8. The files citing that evidence are the same, kind for kind, in both wikis. The other two runs on the copy were right.
- **The question on the merge is a drop the rule does not flag.**
  - It has two evidence episodes. All 6 sessions on the cut's wiki opened and cited both. All 3 on the copy opened and cited the first and never opened the second.
  - In the cut's wiki the second is cited by a person's page, a topic and a day log. In the copy it is cited by the topic and the day log only: the person's page that cited it is gone, and no other person's page cites it.
  - Tier 5 still shows no drop, because two other tier-5 questions were wrong more often on the cut's wiki than on the copy, and a tier's drop is a mean.
- **The tier-5 sweep that was wrong in all 6 runs on the cut's wiki** had all its evidence opened and cited in every one of the 9 runs, on both wikis, and the files citing it are the same by kind. The difference is in the answers or the judging, which the counts can't show.
- **The rule has two blind spots this run shows.** A tier the first wiki gets entirely right has a floor of zero, so one miss fails it. And a question that falls in every run can hide behind gains elsewhere in its tier.

## The first comparison, on the v0.13 set

#140's first criterion:

> Run files plus wiki over the cut 3 times with the cut's own wiki and 3 times with the dreamed copy, at the same commit. It passes when overall correctness with the dreamed copy is at least the undreamed one's, and no category drops by more than the run-to-run spread.

All runs ran in `pa-home` at commit `fe227b3` on 2026-11-22, with Sonnet answering and judging, over the cut of 1616 episodes with the v0.13 set of 26 questions. No question failed to run in any set.

| Set | Runs | Correctness, run by run | Wrong answers | Report |
|---|---|---|---|---|
| The cut's own wiki | 3 | 0.96, 1.00, 1.00 | 1 of 78 | |
| The dreamed copy | 3 | 0.88, 1.00, 0.96 | 4 of 78 | `real-data.md`: fails |
| The dreamed copy, again | 3 | 0.96, 0.96, 0.96 | 3 of 78 | `real-data-again.md`: fails |

- **`real-data.md` is the criterion's comparison.** It fails on overall correctness (0.95 against 0.99), on single-fact recall (0.94 against 1.00, one answer of 18), and on where things stand (0.83 against 1.00, one answer of 6). Open loops is lower too, 0.33 against 0.67, inside its spread.
- **Evidence recall** is 0.93 on the cut's wiki and 0.92 and 0.91 on the copy. **Citation validity** is 0.99 against 0.97 and 0.99. **Cost** is $7.53 for 3 runs against $7.35 and $7.54.
- **Three questions account for every wrong answer** in the 9 runs. The other 23 were right every time on both wikis.

| Question | The cut's own wiki, 3 runs | The dreamed copy, 6 runs |
|---|---|---|
| One single-fact question | 3 right | 5 right, 1 wrong |
| One where-things-stand question | 3 right | 4 right, 2 wrong |
| The open-loops question | 2 right, 1 wrong | 2 right, 4 wrong |

## The two questions, asked 8 more times

The where-things-stand and open-loops questions were asked alone, 8 times with each wiki (`real-data-two-questions.md`), to tell a drop from noise.

| Question | The cut's own wiki | The dreamed copy |
|---|---|---|
| Where things stand, these 8 runs | 7 right of 8 | 6 right of 8 |
| Open loops, these 8 runs | 4 right of 8 | 4 right of 8 |
| Where things stand, every run | 10 right of 11 (0.91) | 10 right of 14 (0.71) |
| Open loops, every run | 6 right of 11 (0.55) | 6 right of 14 (0.43) |

- **The open-loops question is a coin flip on either wiki.** The first set's 2 of 3 on the cut's wiki was the lucky side of it.
- **The where-things-stand question is lower on the copy**, 4 misses in 14 against 1 in 11. That many answers can't tell a real difference of that size from chance.

## What the counts say about the cause

Each of these is a count over the run files of every run above, made without printing an answer. The copy has 13 wrong answers in all (8 on the open-loops question, 4 on the where-things-stand question, 1 on the single-fact question), and the cut's own wiki 6 (5 and 1).

- **No wrong answer's evidence is on a page dreaming changed or removed**, on either wiki. The episodes those two questions' wrong answers failed to cite are cited by one day log each and by no page, in both wikis alike.
- **Every wrong answer on those two questions is a search miss**, on both wikis: the session never opened the episode it failed to cite. The single-fact miss cited all its evidence.
- **No session behind a wrong answer opened a page that was retired or merged.**
- **The judge's reasoning mentions a proposal or the librarian in 1 of the copy's 13 wrong answers.**
- **The open proposals do reach answers.** In the two full sets on the copy, 125 of 156 sessions read `proposals.md`, and 15 answers name a proposal by its id. On the where-things-stand question, answers that name one were right 2 times of 4, and answers that don't 8 times of 10. That is too few to call.

## What is left for the owner

- **The private analyses** are under the data root in `evals/`. `v0.14-graded-private.md` is the graded comparison's: it opens with the questions whose score fell, and has every question run by run and each failed answer. `v0.14-dreaming-private.md`, `v0.14-dreaming-again-private.md` and `v0.14-dreaming-two-questions-private.md` are the first comparison's.
- **The copy** is in `replays/v0.14-cut-dreamed/` under the data root, with its `proposals.md` and `retired.md`, and its history in `wiki.git/` beside it.
- **Going live (#153) waits on the owner's approval** in a comment on #152.
