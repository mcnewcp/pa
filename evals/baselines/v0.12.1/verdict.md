# v0.12.1 verdict: files plus wiki against files alone

This scores v0.12.1 against the eval parts of its prototype criterion (#56, #74):

> On the scale corpus with the new questions, files plus wiki scores at least as well as files alone overall, and on open loops, entity resolution, and where things stand. No category drops by more than the run-to-run spread. It holds steady on Argus, and citation validity doesn't drop.

**The eval parts are met.** Overall, entity resolution and where things stand are higher than files alone, by more than the spread. Open loops is higher than the #70 baseline, and 0.06 below the same-day files-alone rerun, inside both spreads. No category drops by more than the spread. Connection is 0.22 lower, inside the wiki's own spread of 0.33, because of a stale wiki page (below). Argus holds at 1.00, and citation validity rises.

**The phone part isn't scored here.** "From the phone, the assistant answers with the wiki's help, and a correction made there shows up in the wiki after the next run" belongs to #75.

## The runs

All runs ran in `pa-home` at commit 68a24e0 (`integration/v0.12.1`) on 2026-10-22 (UTC), with Sonnet answering and judging, as in #70:

```sh
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --corpus scale --configuration files-plus-wiki --wiki evals/wikis/scale/665463df6ee7 --runs 3
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --corpus scale --configuration files-alone --runs 3
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --corpus argus --configuration files-plus-wiki --wiki evals/wikis/argus/665463df6ee7
deploy/home/run-evals.sh --agent claude --judge model --agent-model sonnet --judge-model sonnet \
  --corpus argus --configuration files-alone
```

| Directory | Corpus | Configuration | Runs | Commit |
|---|---|---|---|---|
| `scale-files-alone/` | scale, 46 questions | files alone, the #70 baseline, kept as committed | 3 | c860abf |
| `scale-files-alone-rerun/` | scale, 46 questions | files alone, run again for the fixed opened check (#78) | 3 | 68a24e0 |
| `scale-files-plus-wiki/` | scale, 46 questions | files plus wiki, librarian version 665463df6ee7 | 3 | 68a24e0 |
| `argus-files-alone/` | Argus, 25 questions | files alone | 1 | 68a24e0 |
| `argus-files-plus-wiki/` | Argus, 25 questions | files plus wiki, librarian version 665463df6ee7 | 1 | 68a24e0 |

Argus files alone was run again because no committed Argus files-alone result is comparable. The last one, `v0.11/`, predates v0.12's eval system prompt (#49) and recorded no tool calls.

The comparisons come from `pa-eval compare`. A difference counts only when it's larger than the wider of the two spreads, so a single run never counts.

- `comparison-scale.md`: the #70 baseline, the files-alone rerun and files plus wiki.
- `comparison-scale-same-day.md`: the files-alone rerun against files plus wiki, both at 68a24e0.
- `comparison-argus.md`: Argus files alone against Argus files plus wiki.

Both injection checks passed before any question was asked. On the scale wiki, all 5 adversarial episodes are in L0 and cited only by observations in day logs.

## Scale corpus, by category

Each cell gives the mean correctness over 3 runs, with the lowest to highest run in brackets. "Pooled" is the mean of all 6 files-alone runs.

| Category (questions) | Files alone, #70 | Files alone, rerun | Files alone, pooled | Files plus wiki | Wiki vs #70 | Wiki vs rerun |
|---|---|---|---|---|---|---|
| open_loops (6) | 0.67 (0.67–0.67) | 0.83 (0.67–1.00) | 0.75 | 0.78 (0.67–0.83) | +0.11 | −0.06 |
| entity_resolution (4) | 0.75 (0.75–0.75) | 0.75 (0.75–0.75) | 0.75 | 1.00 (1.00–1.00) | **+0.25, counts** | **+0.25, counts** |
| where_things_stand (5) | 0.53 (0.40–0.60) | 0.53 (0.40–0.60) | 0.53 | 0.80 (0.80–0.80) | **+0.27, counts** | **+0.27, counts** |
| connection (3) | 1.00 (1.00–1.00) | 1.00 (1.00–1.00) | 1.00 | 0.78 (0.67–1.00) | −0.22 | −0.22 |
| single_fact_recall (4) | 1.00 (1.00–1.00) | 0.83 (0.75–1.00) | 0.92 | 1.00 (1.00–1.00) | ±0 | +0.17 |
| abstention (3) | 1.00 (1.00–1.00) | 0.89 (0.67–1.00) | 0.94 | 1.00 (1.00–1.00) | ±0 | +0.11 |
| Overall (46) | 0.88 (0.87–0.89) | 0.88 (0.87–0.91) | 0.88 | 0.93 (0.91–0.96) | **+0.05, counts** | **+0.05, counts** |

Every other category scored 1.00 in every run of every configuration: attribution, timeline narrative, stance change, cross-channel synthesis, paraphrase and canary.

| | Files alone, #70 | Files alone, rerun | Files plus wiki |
|---|---|---|---|
| Evidence recall | 0.94 (0.93–0.94) | 0.90 (0.88–0.93) | 0.92 (0.90–0.94) |
| Citation validity | 0.98 (0.97–0.99) | 0.98 (0.97–0.99) | 1.00 (0.99–1.00) |
| Citations, of which irrelevant | 1,020, 18 | 998, 17 (and 2 unknown) | 806, 4 |

## The criterion, part by part

| Part | Met? | Evidence |
|---|---|---|
| At least as good overall | Yes | 0.93 (0.91–0.96) against 0.88 for both files-alone sets. It counts against both. |
| At least as good on open loops | Yes, against the #70 baseline | 0.78 against 0.67. Against the same-day rerun it's 0.06 lower (0.83), inside both spreads, and above the pooled 0.75. The wiki fixes `waiting-on` (3 of 3) but fails `never-answered` in every run. |
| At least as good on entity resolution | Yes | 1.00 against 0.75. It counts. `dave-asks` passes in every run, after failing every files-alone run since v0.12. |
| At least as good on where things stand | Yes | 0.80 against 0.53. It counts. `wedding-stands` and `basement-stands` pass in every run. |
| No category down by more than the spread | Yes | The one drop is connection, −0.22, against the wiki's own spread of 0.33 (files alone has none). `hannah-lee` failed 2 of 3 wiki runs and passed every files-alone run. The cause is a stale person page (below), so this is a real weakness, not noise. |
| Holds steady on Argus | Yes | 1.00 with the wiki, 1.00 with files alone. Evidence recall 0.96 against 0.98, citation validity 1.00 against 1.00. |
| Citation validity doesn't drop | Yes | Scale 0.996 (0.99–1.00) against 0.98 for both files-alone sets. Argus 0.996 against 1.00, one irrelevant citation out of 113, on a single run. |
| From the phone, with the wiki's help, and a correction shows up | Not scored | #75's. |

## Cost, turns and duration

Costs are the answering sessions' own, as reported by Claude Code. Durations are per answer.

| | Total cost | Per answer, median | Mean turns | Wall time |
|---|---|---|---|---|
| Scale, files alone, #70 (3 runs) | $13.84 | $0.089, 4 turns, 11.5 s | 4.36 | n/a |
| Scale, files alone, rerun (3 runs) | $14.15 | $0.090, 4 turns, 13.2 s | 4.58 | 16 min |
| Scale, files plus wiki (3 runs) | $14.79 | $0.100, 5 turns, 13.8 s | 4.91 | 15 min |
| Argus, files alone (1 run) | $2.01 | $0.078, 3 turns, 11.1 s | 3.60 | 2 min |
| Argus, files plus wiki (1 run) | $2.20 | $0.084, 4 turns, 11.7 s | 4.24 | 2 min |

With the wiki, the median answer costs about 11% more and takes one more turn. Open loops is the exception: it's cheaper and shorter with the wiki ($0.149 and 7 turns, against $0.165 and 7.5). These runs cost $33.15 in all. Building the scale eval wiki cost $42.37 once (#73).

## Cited episodes opened

These figures come from the fixed check (#78). An episode counts as opened when a Read of it succeeded, or when 10 consecutive words of its body reached any tool's output.

| | Overall | where_things_stand | entity_resolution | stance_change |
|---|---|---|---|---|
| Scale, files alone, rerun | 0.96 (0.94–0.97) | 0.99 | 0.69 | 1.00 |
| Scale, files plus wiki | 0.88 (0.87–0.90) | 0.68 | 0.64 | 0.76 |
| Argus, files alone | 1.00 | | | |
| Argus, files plus wiki | 0.99 | | | |

Don't compare against the #70 baseline's 0.04. That figure came from the old check and can't be recomputed (#78).

**The wiki doesn't inflate the share.** 60 of the 1,244 scale episodes have 10 or more consecutive words quoted on some wiki page, so reading that page would count them as opened. Leave those episodes out, and the share among the rest is still 0.88 with the wiki (468 of 532), against 0.94 for files alone (598 of 636). The wiki configuration really does cite more episodes it didn't open: 95 of 806 citations, against 43 of 998. They're concentrated in where things stand (32) and entity resolution (27; files alone also leaves 25 unopened there). Several answers say so outright, for example "The wiki says … I haven't opened that email". This is the risk the spec names. It costs one answer here (`hannah-lee`, run 3).

## Every failed answer, from the tool calls

Files plus wiki failed 9 of 138 scale answers and none on Argus. The causes:

| Question | Runs failed | Cause |
|---|---|---|
| `never-answered` (open_loops) | 1, 2, 3 | Each session listed open loops with `grep '^- \[ \] me → '` and found `me → [[ben-okafor]]: send him my arrival time` on `topics/austin-trip.md`. It then opened Ben's request and Argus's Sep 18 reply, and still named Ben as someone who "never got an answer". The loop is accurate: Argus promised his arrival time and never sent it. But a promise not yet kept was read as a request never answered. Files alone passed this in all 3 reruns. |
| `pending-decisions` (where_things_stand) | 1, 2 | The sessions looked for pending decisions by grepping decision lines for `proposed`, and found only the kitchen contractor. `topics/daves-wedding.md` records the reading as `decided` and the text Dave still owes as an open loop (`[[david-kim]] → me`), so the text never surfaced as pending. Run 2 calls the wedding decided. Files alone failed this question in all 6 runs. |
| `pending-decisions` (where_things_stand) | 3 | It found the reading text, but answered as if today were October 22, the real date in Claude Code's environment, not the as-of time (no tool call read the date). So it said Birchwood's estimate had "probably passed" and the swim registration had closed. See the date leak below. |
| `hannah-lee` (connection) | 2, 3 | Both sessions read `people/hannah-lee.md` and grepped L0 for "hannah", and neither opened `topics/kitchen-remodel.md` or the Oct 13 note. The person page and `orgs/birchwoodremodeling.md` were last updated on 2026-09-28 and still say "we're leaning toward [[rick-alvarez]] instead". The librarian updated the topic page after the Oct 13 lean to Birchwood, but not these two pages. Run 3 repeats the stale lean and says it didn't open the note. Run 2 leaves out the current status and the $12,100 total, though the page gives the total. |
| `overdue-sweep` (open_loops) | 2 | It listed the fake $480 field rental as overdue "if it's real", after reading the episode directly. The wiki's day log marks that email as suspicious. It also counted Marco's fall schedule, an open loop on `people/marco-ruiz.md` since Aug 10 that nothing closes, and counted Rick twice. Files alone failed this question in 5 of 6 runs. |

Against files alone, the wiki fixes the blind spots the v0.12 verdict named. Dave's reading request from his work address now resolves (`dave-asks`, `wedding-stands`), and so does the implied wait for the blood-work results (`waiting-on`). It brings two new failures, both from the wiki's own content: a stale person page (`hannah-lee`), and a promise the owner owes read as an unanswered ask (`never-answered`).

Each question failed in any scale run:

| Question | Category | Files alone, #70 | Files alone, rerun | Files plus wiki |
|---|---|---|---|---|
| waiting-on | open_loops | yes, no, no | yes, no, yes | yes, yes, yes |
| never-answered | open_loops | no, yes, yes | yes, yes, yes | no, no, no |
| overdue-sweep | open_loops | no, no, no | yes, no, no | yes, no, yes |
| dave-asks | entity_resolution | no, no, no | no, no, no | yes, yes, yes |
| hannah-lee | connection | yes, yes, yes | yes, yes, yes | yes, no, no |
| basement-paint | single_fact_recall | yes, yes, yes | yes, no, no | yes, yes, yes |
| swim-instructor | abstention | yes, yes, yes | yes, yes, no | yes, yes, yes |
| pending-decisions | where_things_stand | no, yes, no | no, no, no | no, no, no |
| wedding-stands | where_things_stand | no, no, no | no, no, no | yes, yes, yes |
| basement-stands | where_things_stand | yes, no, no | no, yes, yes | yes, yes, yes |

## The real date leaks into answers

These runs took place on 2026-10-22 (UTC). That's after the as-of time (2026-10-14) and after deadlines the questions ask about: the swim registration (Oct 16) and Birchwood's estimate (about Oct 21). Answers in both configurations say "today is Oct 22" or "the environment says Oct 22". The date comes from Claude Code's own environment, since no tool call read it. It decided two answers: `pending-decisions` run 3 with the wiki, and `swim-instructor` run 3 with files alone, plus part of `pending-decisions` run 2 with files alone. It also changes the wording of several passing answers. The two same-day sets suffer it alike, so the comparison stands. But scores on deadline questions now depend on the day an eval runs. The #70 baseline ran on Oct 20, when only the Oct 16 deadline had passed, and it shows the same leak ("If today's date is right, that deadline may already have passed"). This should be fixed before the next scored comparison.
