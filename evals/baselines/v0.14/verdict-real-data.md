# v0.14 on real data: the dreamed copy of the v0.13 cut's wiki against the cut's own

Numbers only. The questions, answers and failure analysis are in the private analyses under the data root, for the owner. This file was written from counts, scores, exit codes and the diagnostician's released findings; no page, proposal or answer was read to write it.

#152 has been compared three times.

- **The rerun, after four fixes, passes.** Over 53 questions in tiers 0 to 9, a copy dreamed by the fixed librarian scores 0.97 against the cut's own wiki's 0.97. The drop is 0.006 against a noise floor of 0.025, every tier is within its own floor, and no question fell. A diagnosis of the copy's changes found no answer lost to dreaming and every fold complete in its facts. It also found one loop closed on an episode that only implies the thing was done, which the instructions already forbid: of 10 sessions that judged that evidence, real or reproduced, it is the only one that did.
- **The graded comparison before the fixes failed in one tier by one answer,** and one tier-5 question was wrong in every run on the copy. Two diagnoses traced that question to a page the first dreaming run merged away, and #169 and #170 fixed the cause.
- **The first comparison, by #140's criterion on the 26-question v0.13 set, failed** on three unstable questions: 0.95 against 0.99. It could not tell the wikis apart, which is why #156 replaced the set and the rule.

## The rerun, after the fixes

### What changed since the graded comparison

Two diagnoses of the graded comparison (ADR-0010, #158) were released on 2026-11-25, and the owner's outside session read both against their private analyses. They found one fault in dreaming and one in how the assistant looks for open loops. Four tickets followed, all merged and closed:

| Ticket | What it changed | Checked by |
|---|---|---|
| #169 | Dreaming is resumed for, and fails on, only the lint errors its own run caused. The host accepts a merge only under an applied duplicate candidate that names both pages, and refuses a retirement while an open loop on any page names the page. | Host tests; the dreaming fixture, 3 runs of 3 |
| #170 | The assistant starts from the wiki's open loops when asked what the owner is waiting on or has promised, and covers every request when asked what someone needs. | The numbers below |
| #171 | A fallen question fails a comparison, and a tier passes when its drop is one answer or less (the one-answer allowance). | Tests |
| #172 | A retired page is folded into the kept page line by line. | A released reproduction: the fold was complete in 1 run of 3 before and 3 of 3 after |

**#170 on real data**, on the first dreamed copy, with the two questions it bears on asked 9 times before and 9 after:

| | Before | After |
|---|---|---|
| The question that fell in the graded comparison | 8 right of 9 | 9 right of 9 |
| Its sessions that ran the open-loop search | 0 of 9 | 8 of 9 |
| The question on what someone needs | 3 right of 9 | 6 right of 9 |

The first row also shows how little three runs say: the question that was wrong 3 times of 3 in the graded comparison was right 8 times of 9 on the same copy, with the same instructions, two days later.

### The dreaming runs

Two fresh copies of the cut's wiki were dreamed in the librarian's VM, at the cut's as-of time, with Claude Opus 5.5 at medium effort. The second copy is by librarian version `027843bdd4bd` (after #169), and the third by `7583ca0ee9b1` (after #172), which is the version the live nightly job now runs.

| | The second copy | The third copy |
|---|---|---|
| Candidates judged | 29 | 29 |
| Applied | 4 | 6 |
| Proposed | 2, as 2 open proposals | 2, as 2 open proposals |
| Dismissed | 23 | 21 |
| Pages retired | 2 | 3 |
| Pages merged | 0 | 0 |
| Other pages changed | 4 | 6 |
| Cost | $1.24 | $1.63 |
| Turns | 64 | 80 |
| Resumes | 0 | 0 |
| Lint warnings after the run | 6 | 5 |

| Candidate kind | Offered | The second copy: applied, proposed, dismissed | The third copy: applied, proposed, dismissed |
|---|---|---|---|
| stale | 3 | 2, 0, 1 | 2, 0, 1 |
| loop-past-due | 5 | 0, 0, 5 | 1, 0, 4 |
| loop-aging | 1 | 0, 0, 1 | 0, 0, 1 |
| unengaged | 20 | 2, 2, 16 | 3, 2, 15 |

- **Neither run merged a page or was resumed.** The first dreaming run, before the fixes, merged one and was resumed once.
- **The cut's wiki has lint errors from a rule added after the cut.** Each run left the one whose page is still there, for the next consolidation, and neither run was failed for it.
- **Of the 44 pages,** the second copy has 42 and the third 41. The third retired 1 person's page and 2 organizations', and changed 2 topics and 4 organizations.
- **The host's checks were tightened once more after the third copy was dreamed** (`c86e796`, from a code review): a merge that came on a resume, and a retirement after the session rewrote the loop that named the page, could each get past #169's checks. Neither happened in the third run, which wasn't resumed, and whose diagnosis found no open loop naming a retired page. The librarian's instructions didn't change.

### The comparison

#152's criterion, as #171 amended #156's:

> Run files plus wiki over the cut 6 times with the cut's own wiki and 3 times with the dreamed copy, at the same commit, asking #156's graded set. It passes when no question fell and the drop is no larger than the noise floor or the one-answer allowance, overall and in every tier.

All 12 runs ran in `pa-home` at commit `153446c` on 2026-11-25 and 26, with Sonnet answering and judging, over the same cut. The assistant's instructions are the same at that commit as at the branch's head. No answer failed to run in any of them. One more batch of 3 on the cut's wiki was cut off by the account's usage limit, with 142 of its 159 answers not run; it is left out, and the batch was run again. The report is `real-data-rerun.md`, for the third copy.

| Set | Runs | Correctness, run by run | Wrong answers | Cost |
|---|---|---|---|---|
| The cut's own wiki | 6 | 0.96, 0.96, 0.96, 0.98, 1.00, 0.98 | 8 of 318 | $29.32 |
| The second copy | 3 | 0.92, 0.98, 0.98 | 6 of 159 | $15.15 |
| The third copy | 3 | 0.98, 0.96, 0.96 | 5 of 159 | $14.73 |

The third copy, which is the comparison that counts:

| | Questions | The cut's own wiki | The third copy | Drop | Noise floor | Within it |
|---|---|---|---|---|---|---|
| Overall | 53 | 0.97 | 0.97 | 0.006 | 0.025 | yes |
| Tiers 0 to 3 | 22 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 4 | 5 | 0.93 | 1.00 | -0.067 | 0.133 | yes |
| Tier 5 | 7 | 0.86 | 0.76 | 0.095 | 0.190 | yes |
| Tiers 6 to 9 | 19 | 1.00 | 1.00 | 0.000 | 0.000 | yes |

- **It passes, and without the one-answer allowance:** every tier is within its own noise floor, and no question fell.
- **Evidence recall** is 0.97 on both wikis, and **citation validity** 0.98 on both.

The second copy, against the same 6 runs:

| | Questions | The cut's own wiki | The second copy | Drop | Noise floor | Within it |
|---|---|---|---|---|---|---|
| Overall | 53 | 0.97 | 0.96 | 0.013 | 0.025 | yes |
| Tier 0, tiers 2 and 3 | 16 | 1.00 | 1.00 | 0.000 | 0.000 | yes |
| Tier 1 | 6 | 1.00 | 0.94 | 0.056 | 0.000 | only by the allowance (0.056) |
| Tier 4 | 5 | 0.93 | 0.93 | 0.000 | 0.133 | yes |
| Tier 5 | 7 | 0.86 | 0.81 | 0.048 | 0.190 | yes |
| Tiers 6 to 9 | 19 | 1.00 | 1.00 | 0.000 | 0.000 | yes |

- **It passes, in tier 1 only by the allowance:** one wrong answer of 18, where the cut's wiki got all 36 right. By the rule as the owner approved it on #156, without the allowance, the second copy fails tier 1.

### The seven questions

Seven questions account for all 19 wrong answers in the 12 runs. The other 46 were right every time.

| Question | The cut's own wiki, 6 runs | The second copy, 3 runs | The third copy, 3 runs |
|---|---|---|---|
| A tier-1 question | 0 wrong | 1 wrong | 0 wrong |
| A tier-4 sweep | 2 wrong | 1 wrong | 0 wrong |
| A tier-5 question whose one evidence episode no page cites | 3 wrong | 1 wrong | 3 wrong |
| A tier-5 question with three evidence episodes | 0 wrong | 1 wrong | 1 wrong |
| The tier-5 question on what someone needs | 0 wrong | 2 wrong | 0 wrong |
| A tier-5 question with two evidence episodes | 2 wrong | 0 wrong | 1 wrong |
| Another tier-5 question | 1 wrong | 0 wrong | 0 wrong |

Three of them were asked alone 9 more times with each wiki, since three runs can't tell a drop from chance:

| Question | The cut's own wiki, every run | The copy, every run |
|---|---|---|
| The one no page cites the evidence of, on the third copy | 8 right of 15 | 5 right of 12 |
| The one on what someone needs, on the second copy | 15 right of 15 | 9 right of 12 |
| The one with two evidence episodes, on the second copy | 13 right of 15 | 11 right of 12 |

- **The question that was wrong 3 times of 3 on the third copy is a coin flip on either wiki.** Its 9 extra runs were right 5 times with each. Its evidence is cited by one day log and no page, the same file in both wikis, and every wrong session on either wiki never opened it.
- **For all three questions with a wrong answer on the third copy,** the files that cite their evidence are the same in the copy as in the cut's wiki.

### What the diagnoses found

**The second copy** (`rerun-drop`, released on the first pass with all 16 checks at 0; $5.59, 112 turns). It answered the referral fully: no answer was lost to dreaming, every applied line is cited and true to its episodes, no loop was dropped, both proposals are sound, and no dismissal is plainly wrong. Its four findings:

| Finding | Kind | Where | What followed |
|---|---|---|---|
| The misses on what someone needs are chance, at about 0.19 | noise | the assistant's answer | None |
| The judge scored the same framing of an answer both ways | noise | the judge | #174, outside this spec |
| The tier-1 miss: a session read only the head of the index, and the host's index order had moved the line it needed | fault | the assistant's navigation | #173, outside this spec. The index order is the host's, and every librarian run writes it, so dreaming's edits were not the cause |
| A retirement's fold left one line's detail out. No answer was lost | fault | dreaming | #172, with its reproduction |

**The third copy** (`third-copy`, released on the first pass with all 16 checks at 0; $4.10, 85 turns). It answered the referral fully: no change of the run cost an answer, each of the three folds carried every fact, the line the second copy's fold lost is on the kept page with the right date, and no open loop named a retired page. Its four findings:

| Finding | Kind | Where | What followed |
|---|---|---|---|
| A past-due loop was closed on an episode that only implies the thing was done. Nothing in L0 records it | fault | dreaming | The follow-up below |
| The same edit joined two lines and left out a clause one of them held, keeping the citation that backed it | fault | dreaming | Not fixed: no reproduction |
| A fold carried every fact of a retired page's line and left out who sent it and on which day | fault, at low confidence | dreaming | Not fixed: no reproduction |
| The three tier-5 questions with a wrong answer touch nothing the run changed | noise | the assistant's answer | None |

**The closed loop, followed up** (`third-copy-f1`, released on the first pass; $3.77, 87 turns).

- **The first reproduction didn't fail.** Dreamed 3 times by the librarian that made the third copy, it was dismissed and the loop left open 3 times of 3. The second copy's run had dismissed the same candidate on the real wiki.
- **The follow-up withdrew the finding as a gap in the instructions,** which already say to close a loop only on an episode that shows the thing done, and that a guess settles nothing. It kept it as a lapse of one session that the host doesn't check: the host accepts any cited episode as what closed a loop, and a closed loop isn't offered to a later run.
- **A second reproduction, nearer the real page's shape, didn't fail either.** Dreamed 5 times, it was dismissed and the loop left open 5 times of 5. So of 10 sessions that judged this evidence, real or reproduced, one closed the loop, and it was one of the two on the real wiki.
- **It isn't fixed here.** The follow-up's suggestion is a host check on what closes a loop, which would also refuse some closures that are right. That trade is in #175, outside this spec, and whether the lapse holds up going live is the owner's to say.

### The one-answer allowance is the owner's to keep or drop

#171 added the allowance after the graded comparison failed a tier on one answer of 18. It loosens the rule the owner approved on #156. The third copy doesn't need it. The second copy passes tier 1 only by it, on a miss the diagnosis traced to the assistant and the index, not to dreaming. Dropping it would leave the third copy's result as it is.

## The graded comparison, before the fixes

### The first dreaming run

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

### The comparison

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

### What the counts said

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

- **The private analyses** are under the data root in `evals/`. `v0.14-rerun-private.md` is the third copy's comparison: it opens with the questions whose score fell, and has every question run by run and each failed answer. `v0.14-rerun-second-copy-private.md` is the second copy's, and `v0.14-graded-private.md` the graded comparison's before the fixes. `v0.14-dreaming-private.md`, `v0.14-dreaming-again-private.md` and `v0.14-dreaming-two-questions-private.md` are the first comparison's.
- **The diagnoses' private analyses** are under the data root in `diagnoses/`, one directory each, in `output/analysis.md`: `rerun-drop` for the second copy and `third-copy` for the third.
- **The copies** are in `replays/` under the data root: `v0.14-cut-dreamed-3/` is the third, `v0.14-cut-dreamed-2/` the second, and `v0.14-cut-dreamed/` the first. Each has its `proposals.md` and `retired.md`, and its history in `wiki.git/` beside it.
- **Whether the one-answer allowance stays** is the owner's decision, above.
- **Whether the loop closed without a record of the act holds up going live** is the owner's too (#175). Nothing reproduces it, so nothing was changed for it, and the librarian's instructions that were compared are the ones at the branch's head.
- **Going live (#153) waits on the owner's approval** in a comment on #152.
