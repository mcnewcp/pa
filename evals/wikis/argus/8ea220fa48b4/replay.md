# Argus eval wiki, librarian version 8ea220fa48b4

Built on 2026-11-21 by `PA_LIBRARIAN_OWNER_ENV=evals/corpus/owner.env deploy/home/librarian.sh replay … --corpus evals/corpus/payloads --as-of 2026-10-14T20:00`, in `pa-librarian`, with Claude Opus 5.5 at medium effort. It is v0.14's replay of Argus (#151): the first eval wiki built under the page-creation rules of #142.

- **Batches:** 6 weekly batches, from 2026-09-07 to 2026-10-14. 64 of 66 episodes were offered; selection skipped 2.
- **Cost:** $7.02 over 12 sessions and 352 turns, with one resume (a consolidation session, in the second batch).
- **A failed run:** the third batch's first run hit the account's usage limit in its consolidation session and was discarded ($0.37). The replay was carried on with `--resume` two and a half hours later, and that batch committed. `run-log.jsonl` keeps the failed entry.
- **The wiki:** 9 people, 8 organizations, 5 topics, and 47 day logs, the same counts as at `665463df6ee7`. One page differs in kind: the YMCA's aquatics team, a shared mailbox the owner wrote to, has a person page (`people/westside-ymca-aquatics.md`) beside the YMCA's organization page. Priya, who has a page at `665463df6ee7`, has none: nothing links her slug.
- **Lint warnings after the last run:** 1.
- **Injection checks:** Argus has no adversarial episodes, so they pass trivially.
- **Scores:** 1.00 on all 3 files-plus-wiki runs at `fe227b3` (`evals/baselines/v0.14/argus-replayed/`).
