# v0.14 on real data: the dreamed copy of the v0.13 cut's wiki against the cut's own

Numbers only. The questions, answers and failure analysis are in the private analyses under the data root, for the owner. This file was written from counts, scores and exit codes; no page, proposal or answer was read to write it.

#152's criterion, from #140:

> Run files plus wiki over the cut 3 times with the cut's own wiki and 3 times with the dreamed copy, at the same commit. It passes when overall correctness with the dreamed copy is at least the undreamed one's, and no category drops by more than the run-to-run spread.

**The comparison fails as written.** Over 3 runs each, the dreamed copy scores 0.95 against 0.99: four wrong answers of 78 against one. More runs put the drop on two questions that fail on the cut's own wiki about as often, so it reads as run-to-run noise, but that is the owner's call to make from the private analyses.

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

## The comparison

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

- **The private analyses**, one for each comparison above, are under the data root in `evals/`: `v0.14-dreaming-private.md`, `v0.14-dreaming-again-private.md`, and `v0.14-dreaming-two-questions-private.md`. Each has every question run by run and each failed answer.
- **The copy** is in `replays/v0.14-cut-dreamed/` under the data root, with its `proposals.md` and `retired.md`, and its history in `wiki.git/` beside it.
- **Going live (#153) waits on the owner's approval** in a comment on #152.
