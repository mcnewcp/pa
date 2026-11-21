# v0.14 baseline: a dreamed wiki against an undreamed one

This scores dreaming on the two fictional corpora, against #151's criteria (from #140):

> **Scale.** Dream a copy of the scale eval wiki. Run files plus wiki 3 times on the undreamed wiki and 3 times on the dreamed copy, at the same commit. It passes when overall correctness on the dreamed copy is at least the undreamed one's; no category drops by more than the run-to-run spread; the question that failed on stale cross-page state in the v0.12.1 verdict passes on the dreamed copy; the injection checks pass on the dreamed copy.
>
> **Argus.** Replay the Argus eval wiki at the new librarian version, dream it once, and score it 3 times against its baseline. No category drops by more than the spread.

**Neither comparison passes as written, and both fail on the same rule.** `pa-eval compare` fails a category that the undreamed wiki got right in every run as soon as the dreamed wiki gets one answer in it wrong.

- **Scale:** the dreamed copy is higher overall (0.95 against 0.93), the stale cross-page question passes in all 3 runs, and the injection checks pass. It fails on entity resolution, 0.92 against 1.00: one answer of 12, which the judge marked wrong while its own reasoning ends "All key facts are present, so the answer is correct."
- **Argus:** the wiki replayed at the new librarian version scores 1.00 in all 3 runs, as the v0.12.1 eval wiki does at this commit. Its dreamed copy scores 0.96 in all 3 runs and 0.96, 1.00, 1.00 in 3 more. Four answers of 150 failed, three of them on one question. Dreaming changed two things in that wiki. Neither is what those answers got wrong, but one of them lost facts: it retired the page of the hospital where the owner's wife works, and her changed shifts went with it.

Nothing was changed after these results. Whether they are good enough to go on to the real-data comparison (#152) is the owner's decision.

## The runs

All eval runs ran in `pa-home` at commit `fe227b3` (`feat/v0.14-dreaming`) on 2026-11-21, with Sonnet answering and judging, through `deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet --configuration files-plus-wiki --runs 3 --corpus <corpus> --wiki <wiki>`. The librarian's version is `8ea220fa48b4`, with Claude Opus 5.5 at medium effort.

| Directory | Corpus | Wiki | Runs |
|---|---|---|---|
| `scale-undreamed/` | scale, 46 questions | `evals/wikis/scale/665463df6ee7` | 3 |
| `scale-dreamed/` | scale | `evals/wikis/scale/665463df6ee7-dreamed-8ea220fa48b4` | 3 |
| `argus-v0.12.1-wiki/` | Argus, 25 questions | `evals/wikis/argus/665463df6ee7`, the v0.12.1 eval wiki | 3 |
| `argus-replayed/` | Argus | `evals/wikis/argus/8ea220fa48b4`, replayed at the new librarian version | 3 |
| `argus-dreamed/` | Argus | `evals/wikis/argus/8ea220fa48b4-dreamed-8ea220fa48b4` | 3 |
| `argus-dreamed-again/` | Argus | the same dreamed copy, run again to see how much of its drop repeats | 3 |

- **`scale-undreamed/run-3` is a replacement.** The set's first third run hit the account's usage limit: 4 questions failed with `You've hit your session limit`. Runs 1 and 2 are that set's; run 3 is one more run of the same wiki at the same commit, made two hours later.
- **Argus's committed baseline is one run** (`v0.12.1/argus-files-plus-wiki`), which has no spread. So the v0.12.1 eval wiki was run 3 times at this commit, and the replayed wiki was run undreamed too, to tell the new page-creation rules from dreaming.

The comparisons are `pa-eval compare`'s: `comparison-scale.md`, `comparison-argus.md` (all four Argus sets), `comparison-argus-dreamed.md` and `comparison-argus-dreamed-again.md` (the replayed wiki against each set of its dreamed copy).

## The dreaming runs

| Copy | Run | Candidates | Applied | Proposed | Dismissed | Left over the cap | Cost | Turns | Resumes |
|---|---|---|---|---|---|---|---|---|---|
| scale | 1 | 30 | 9 | 16, as P1 | 5 | 37 | $1.34 | 53 | 0 |
| scale | 2 | 30 | 7 | 9, as P2 | 14 | 7 | $0.97 | 42 | 0 |
| scale | 3 | 7 | 3 | 0 | 4 | 0 | $0.43 | 17 | 0 |
| scale | 4 | none: all 47 left are held back | | | | | $0 | | |
| Argus | 1 | 3 | 2 | 0 | 1 | 0 | $0.86 | 20 | 1 |

- **Scale: $2.74 for 67 candidates**, about 4 cents a candidate. 19 were applied, 25 proposed, and 23 dismissed.
  - **Run 1** took the 9 stale pages first: 7 were brought up to date, `people/hannah-lee.md` and `orgs/birchwoodremodeling.md` among them, and 2 were dismissed. It reopened the basement flood's status, closed one loop L0 had closed, and asked once about 16 routine loops (5 water bills, 11 library returns and holds).
  - **Run 2** asked once more about 9 routine loops (library, power bills, a bank notice), dismissed 12 loops that L0 can't close, settled 5 quiet pages, and retired 2 unengaged organizations.
  - **Run 3** retired 3 more unengaged organizations and kept 4 (the library, the bank, the power company, the mobile carrier).
  - **The copy** differs from the eval wiki in 20 files: 12 pages changed, 5 organization pages retired (`buildright-supply`, `cloudvault`, `fixit-forum`, `minneapolis-parks-youth-sports`, `parcelpoint`), `proposals.md` with 2 open proposals, `retired.md`, and `index.md`. Lint warnings went from 41 to 40: the past-due loops stay open until the owner answers the two proposals.
- **Argus: $0.86 for 3 candidates.** It brought `people/david-kim.md` up to date (one line about the wedding gift), retired `orgs/st-lukes-hospital.md`, and dismissed a past-due loop.
- **The Argus replay** at `8ea220fa48b4` cost $7.02 over 6 weekly batches, 12 sessions and 352 turns, with one resume ($5.18 at `665463df6ee7`). `evals/wikis/argus/8ea220fa48b4/replay.md` has its record.
- **Injection checks:** all four pass on the dreamed scale copy, the fourth being that no line of `proposals.md` cites an adversarial episode. Argus has no adversarial episodes.

## Scale, by category

Mean over 3 runs, with the lowest and highest run. Categories at 1.00 in every run of both wikis are left out; `comparison-scale.md` has them all.

| Category (questions) | Undreamed | Dreamed | Difference | Undreamed's spread | Within it |
|---|---|---|---|---|---|
| single_fact_recall (4) | 0.92 (0.75–1.00) | 1.00 (1.00–1.00) | +0.08 | 0.25 | yes |
| timeline_narrative (5) | 0.93 (0.80–1.00) | 0.93 (0.80–1.00) | ±0 | 0.20 | yes |
| open_loops (6) | 0.89 (0.67–1.00) | 0.83 (0.67–1.00) | −0.06 | 0.33 | yes |
| entity_resolution (4) | 1.00 (1.00–1.00) | 0.92 (0.75–1.00) | −0.08 | 0.00 | **no** |
| connection (3) | 0.78 (0.67–1.00) | 1.00 (1.00–1.00) | +0.22 | 0.33 | yes |
| where_things_stand (5) | 0.73 (0.60–0.80) | 0.87 (0.80–1.00) | +0.13 | 0.20 | yes |
| Overall (46) | 0.93 (0.87–0.98) | 0.95 (0.93–0.96) | +0.02 | | yes |

| | Undreamed | Dreamed |
|---|---|---|
| Evidence recall | 0.88 (0.85–0.93) | 0.92 (0.91–0.92) |
| Citation validity | 0.997 (0.99–1.00) | 0.989 (0.98–1.00) |
| Cost, 3 runs | $14.41 | $14.28 |
| Mean turns per answer | 5.68 | 5.67 |

## The scale criterion, part by part

| Part | Met? | Evidence |
|---|---|---|
| Overall at least the undreamed wiki's | Yes | 0.95 (0.93–0.96) against 0.93 (0.87–0.98). |
| No category down by more than the spread | **No, by one answer** | Entity resolution: `dave-asks` was marked wrong in run 3 of the dreamed copy, 1 answer of 12, where the undreamed wiki's 12 were all right. The answer lists all three of Dave's asks with their dates. The judge's reasoning for it ends "All key facts are present, so the answer is correct", and its verdict field says false. Open loops is 0.06 lower, inside the spread of 0.33. |
| The stale cross-page question passes | Yes | `hannah-lee` passes in all 3 dreamed runs and fails 2 of 3 undreamed, as in v0.12.1. Dreaming's first run brought both stale pages up to date. |
| Injection checks pass on the dreamed copy | Yes | All four rules. |

## Argus

| Set | Run 1 | Run 2 | Run 3 | Evidence recall | Citation validity | Cost |
|---|---|---|---|---|---|---|
| The v0.12.1 eval wiki | 1.00 | 1.00 | 1.00 | 0.96 | 0.996 | $6.02 |
| Replayed at `8ea220fa48b4` | 1.00 | 1.00 | 1.00 | 0.96 | 1.00 | $5.93 |
| Its dreamed copy | 0.96 | 0.96 | 0.96 | 0.96 | 0.994 | $5.69 |
| Its dreamed copy, again | 0.96 | 1.00 | 1.00 | 0.97 | 0.998 | $5.92 |

- **The new page-creation rules hold on Argus.** The replayed wiki has the same 9 people, 8 organizations, 5 topics and 47 day logs by count, and every answer is right in all 3 runs.
- **The dreamed copy is lower by more than the spread**, since neither undreamed set has any: cross-channel synthesis 0.78 and then 0.89 against 1.00, entity resolution 0.83 and then 1.00 against 1.00.
- **Four answers failed of 150**, on two questions:
  - `saturday-swim-conflicts` failed 3 of 6 runs;
  - `dave-asks` failed 1 of 6.

## Every failed answer on a dreamed wiki

| Question | Wiki | Runs failed | Cause |
|---|---|---|---|
| `never-answered` (open_loops) | scale | 1, 3 | As in v0.12.1: it names Ben Okafor as someone who never got an answer, from the open loop for the arrival time Argus promised him. It fails 1 of 3 undreamed runs the same way. The loop is 27 days old at the as-of time, under lint's 30, so dreaming was never offered it. |
| `pending-decisions` (where_things_stand) | scale | 1, 2 | As in v0.12.1: it finds the kitchen contractor and misses the reading text Dave still owes. The undreamed wiki fails it in all 3 runs. Run 1 also listed the two open proposals as things to decide. |
| `overdue-sweep` (open_loops) | scale | 1 | It missed Sean Brennan's dehumidifiers, listed the fake $480 field rental as possibly overdue, and added Marco's schedule and the water bills, which the first open proposal asks about. The undreamed wiki fails it in 1 of 3 runs. |
| `basement-timeline` (timeline_narrative) | scale | 2 | It gave the insurance side whole and left out the plumber, the dry-out and the rebuild, saying it hadn't checked Birchwood's details. The undreamed wiki fails it in 1 of 3 runs. |
| `dave-asks` (entity_resolution) | scale | 3 | The judge's verdict contradicts its reasoning (above). |
| `saturday-swim-conflicts` (cross_channel_synthesis) | Argus | 1 and 3; 1 of the second set | Run 1 found nothing on who has the kids on Oct 24 and called Nov 14 "tight". Run 3 and the second set's run 1 called Nov 7 a problem, when Mara has the kids that weekend. The undreamed sets pass with answers that hedge the same two points: one "found nothing" on who has the kids, one calls a Saturday a "probable problem" and one a possible conflict, each passed as extra detail. That Mara's parents have the kids is on `people/mara-mcnevans.md` and `topics/daves-wedding.md` in both wikis, unchanged. |
| `dave-asks` (entity_resolution) | Argus | 2 | It opened "Dave has asked you for one thing", the reading, and then gave the RSVP and the hotel block as done without citing the Oct 6 booking. An undreamed run that opened the same way and went on to cover the other two passed. This session read `people/david-kim.md`, the one page dreaming changed, where the added line is about the gift. |

Each question that failed in any scale run:

| Question | Category | Undreamed | Dreamed |
|---|---|---|---|
| basement-paint | single_fact_recall | yes, yes, no | yes, yes, yes |
| basement-timeline | timeline_narrative | no, yes, yes | yes, no, yes |
| dave-asks | entity_resolution | yes, yes, yes | yes, yes, no |
| hannah-lee | connection | no, yes, no | yes, yes, yes |
| never-answered | open_loops | no, yes, yes | no, yes, no |
| overdue-sweep | open_loops | no, yes, yes | no, yes, yes |
| pending-decisions | where_things_stand | no, no, no | no, no, yes |
| wedding-stands | where_things_stand | no, yes, yes | yes, yes, yes |

## What the results say

- **Dreaming fixed what it was built for.** The stale cross-page failure is gone, connection is 1.00, and where things stand is up by 0.13. Evidence recall is up by 0.04.
- **Nothing it did to the scale wiki shows up as a wrong answer.** Four of the five failed answers fail the same way on the undreamed wiki, and the fifth is the judge's slip.
- **The open proposals reach answers.** The assistant now reads `proposals.md`, and in two scale answers it reported proposals as pending decisions or overdue items. Both questions fail on the undreamed wiki too, so this cost nothing here, but the proposals about routine loops are the wiki's questions for the owner, not the owner's own to-dos.
- **Argus's drop is one question on the judge's line.** `saturday-swim-conflicts` turns on whether an answer that hedges Nov 7 or Nov 14 is wrong, and the judge went both ways on answers that say the same thing. It went against the dreamed copy 3 times in 6 and against the undreamed wikis 0 times in 6. That could be chance, and 6 runs can't tell.
- **The pass rule is strict by design** (#150): a category the first wiki never misses allows the second no miss at all. With 3 or 4 questions in a category and 3 runs, one answer decides it.
- **Dreaming retired a page that held facts, on Argus.** `orgs/st-lukes-hospital.md` is where the owner's wife works. The owner never wrote to it, so it was flagged as unengaged, and it held no loop, so the session retired it. The page said her late shifts move to Tuesday and Thursday from the week of October 19, and that her work calendar still shows a shift on Friday, October 23, the day they drive to the wedding. Her own page keeps only "through mid-October". No Argus question asks about her shifts, so no answer shows the loss. The instructions say to fold anything worth keeping into the page it belongs to, and the session folded nothing: her page is unchanged. Their examples of an organization to keep (a bank, a school, a utility, an insurer, a doctor's office) don't name an employer.

## The real date in a session

The dreaming fixture's check (#149) failed once on this branch because two of three sessions dated the pages they changed with the machine's date, a day off the date the run was told. The scale eval wiki holds two pages dated 2026-10-22, the day it was replayed, from the same cause. The host now resumes a session that dates a page another day than the run's (`6b29167`). The dreaming runs above made no such resume on scale; Argus's one resume wasn't looked into.
