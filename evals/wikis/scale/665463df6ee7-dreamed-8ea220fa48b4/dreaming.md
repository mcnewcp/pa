# Scale eval wiki at 665463df6ee7, dreamed by 8ea220fa48b4

A copy of `evals/wikis/scale/665463df6ee7`, dreamed on 2026-11-21 by `PA_LIBRARIAN_OWNER_ENV=evals/corpus/owner.env deploy/home/librarian.sh dream … --corpus evals/corpus/payloads --corpus evals/corpus/payloads-scale`, in `pa-librarian`, with Claude Opus 5.5 at medium effort, as of the replay's end (2026-10-14 20:00 in Chicago). It was run until no candidate was left over the cap of 30. The last three entries of `run-log.jsonl` are the dreaming runs'.

| Run | Candidates | Applied | Proposed | Dismissed | Left over the cap | Cost | Turns | Resumes |
|---|---|---|---|---|---|---|---|---|
| 1 | 30 | 9 | 16, as P1 | 5 | 37 | $1.34 | 53 | 0 |
| 2 | 30 | 7 | 9, as P2 | 14 | 7 | $0.97 | 42 | 0 |
| 3 | 7 | 3 | 0 | 4 | 0 | $0.43 | 17 | 0 |

- **A fourth run** found nothing to judge: all 47 candidates left are held back (the dismissed ones, and the loops the two proposals ask about).
- **Cost:** $2.74 for 67 candidates.
- **Against the undreamed wiki:** 20 files differ: 12 pages changed, 5 unengaged organizations retired (`buildright-supply`, `cloudvault`, `fixit-forum`, `minneapolis-parks-youth-sports`, `parcelpoint`), `proposals.md` with 2 open proposals about 25 routine loops, `retired.md`, and `index.md`.
- **Lint warnings after the last run:** 40, from 41. The routine loops stay open until the owner answers the proposals.
- **Injection checks:** passed, all four rules. All 5 adversarial episodes are in L0, and no page or proposal cites one.
- **Scores:** 0.93, 0.96, 0.96 over 3 files-plus-wiki runs at `fe227b3`, against 0.87, 0.98, 0.93 for the undreamed wiki. `evals/baselines/v0.14/verdict.md` has the comparison and the analysis.
