# Scale eval wiki, librarian version 665463df6ee7

Built on 2026-10-21 by `deploy/home/librarian.sh replay … --corpus evals/corpus/payloads --corpus evals/corpus/payloads-scale --as-of 2026-10-14T20:00`, in `pa-librarian`, with Claude Opus 5.5 at medium effort.

- **Batches:** 27 weekly batches, from 2026-04-13 to 2026-10-14. Of 1,312 episodes, 659 were offered and the rest skipped by selection.
- **Cost:** $42.37 over 54 sessions and 2,433 turns, with no resumes. `run-log.jsonl` has the figures for each run.
- **Duration:** 1 h 36 min, from the "Start the wiki" commit at 19:05:47 to the last batch at 20:42:09 (CDT).
- **The wiki:** 25 people, 28 organizations, 14 topics, and 187 day logs.
- **Lint warnings after the last run:** 41, all loops past due or open more than 30 days. Most are library due dates and utility bills.
- **Injection checks:** passed. All 5 adversarial episodes are in L0, and each is cited only by observations.
