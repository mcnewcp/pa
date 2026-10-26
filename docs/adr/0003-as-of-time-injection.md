# The agent is told "now" in the system prompt and again before the question

The agent's "now" comes from one value: the question's as-of time in evals, the wall clock otherwise (`ClaudeAgent.answer(..., now=None)` falls back to its clock). Claude Code puts the machine's date in every session's context ("Today's date is 2026-10-26.") and that can't be turned off, so the injected time has to override it. Every eval run, over a frozen corpus or real data, uses one method, `both` (`pa_home.agent.AsOfMethod.BOTH`, the default for `ClaudeAgent` and `pa-eval run`):

- The statement goes into Claude Code's system prompt with `--append-system-prompt`, and again into the message, before the question.
- It gives the time (weekday, date, time, UTC offset, and ISO form). It says Claude Code's "Today's date is …" is the machine's clock, not now, and not to use or mention it. And it says to work out today, days left, and whether anything is overdue, has passed, or has happened yet from the as-of date alone (`as_of_statement`).

It was the one method to pass every canary with Sonnet (#79). A statement in the system prompt alone, the method until v0.12.1, let the real date into Sonnet's answers where the date was incidental. A preamble alone got date arithmetic wrong.

Librarian sessions still get the shorter statement (`claude_cli.now_statement`) in the system prompt alone. The librarian version doesn't hash that text, so changing it would change runs without changing the version.

## The experiments

### 2026-10-06: Claude Code 2.1.292, Haiku

There were five canaries over the fixture corpus (`evals/experiments/as_of_canaries.json`), each with an as-of time away from the real date (then 2026-10-06, a Tuesday):

- the day of the week;
- today's date;
- days left before a deadline;
- whether a deadline has passed;
- whether a scheduled event has happened.

The last three need L0 as well as the date. The judge was Sonnet. Counted correct:

| Method | haiku (2 runs) | sonnet (1 run) |
|---|---|---|
| `none` (control: no injection) | 0/10 | 0/5 |
| `system-prompt` | 10/10 | 5/5 |
| `preamble` | 10/10 | 5/5 |

The system prompt won. It keeps the user turn exactly what the owner asked, and the agent didn't credit the date to the owner ("the date you gave me") as it did with the preamble.

### 2026-10-26: Claude Code 2.1.296, Sonnet

The v0.12.1 scored runs (2026-10-22, as-of 2026-10-14) leaked the real date despite the system prompt, as `evals/baselines/v0.12.1/verdict.md` shows ("Today is Oct 22, so it may have just expired"). The rerun ran in `pa-home` with Sonnet answering and judging, on two canary sets:

- **Fixture (5 questions):** the set above. By now the real date was after every date in the fixture corpus, so a question asked after an event got the same answer from either date. Two canaries moved before their events: `hotel-block-open` to Oct 8 (still open, closes tomorrow) and `swim-started` to Oct 14 (not yet, starts Saturday).
- **Scale (6 questions, `as_of_canaries_scale.json`):** questions from the scale corpus whose answers leaked on Oct 22. In each, the date is incidental: "How do I know Hannah Lee?", "Did the cabinet guy ever send me his updated price?", and others. Birchwood's estimate (good until about Oct 21), the swim registration (closes Oct 16), and the blood work results (due Oct 16 to 18) must all still be pending as of Oct 14. A first version that asked about those deadlines directly ("Is Birchwood's estimate still good?") passed every time with the system prompt, so it was replaced.

Counted correct, each set run 3 or 5 times:

| Method | Statement | Fixture | Scale | Answers that used the real date |
|---|---|---|---|---|
| `none` | n/a | 0/5 | 1/6 | all 5 fixture; 4 of 6 scale |
| `system-prompt` | the old one | 15/15 | 16/18 | 2: "That Oct 21 date has already passed as of today (Oct 26)" |
| `preamble` | the old one | 13/15 | 48/48 | 2 opened `hotel-days-left` with "You have no days left … closed on Oct 9", then gave 2 days |
| `system-prompt` | `as_of_statement` | not run | 29/30 | none; the one failure misdated the blood draw |
| **`both`** | **`as_of_statement`** | **15/15** | **30/30** | **none** |

The canaries cost $16.93 in all. To repeat a cell: `deploy/home/run-evals.sh --corpus evals/tests/fixtures/corpus --eval-set evals/experiments/as_of_canaries.json --agent claude --agent-model sonnet --judge model --judge-model sonnet --as-of-method <method> --runs 3`, or `--corpus scale --eval-set evals/experiments/as_of_canaries_scale.json` for the scale set. Run directories land in `evals/runs/` and aren't committed.

## Considered options

- **The system prompt alone,** with the new statement. Rejected: no answer used the real date, but it failed one scale canary and was never run on the fixture set. With the old statement, 2 of 18 scale answers used the real date.
- **A preamble alone.** Rejected: Claude Code's date arrives in the same turn as the preamble ("Your question came with a second date, 2026-10-26"), and twice Sonnet began its answer from it.
- **Keeping the real date out of the session by faking the clock** (libfaketime 0.9.10 under `LD_PRELOAD`). Claude Code reads the date from `new Date()` and has no setting to override it. Under libfaketime, even `claude --version` hangs (2.1.308 on the devbox, with offsets of `+0` and `-12d` and the monotonic clock left real), and ignoring the wait calls aborts it. A faked clock would also break TLS for an as-of time before a certificate's start, so it was dropped.

## Consequences

- The preamble changes the question the agent sees: the owner's question follows a paragraph about the time. Answers may say "the date you gave me". That's harmless in evals. Interactive sessions on the real date are told nothing, so neither the preamble nor the system prompt reaches them.
- Runs before and after #79 are not strictly comparable on deadline questions. Baselines up to v0.12.1 used `system-prompt` with the old statement.
- A first round in 2026-10, before the agent's role was appended to the system prompt, saw Haiku answer every L0 canary as a coding assistant ("I don't have access to your calendar") without searching. So `ClaudeAgent` always appends a short `ROLE` line pointing at CLAUDE.md. Keep it.
- `--as-of-method` stays on `pa-eval run` (default `both`) and the report records it, so a model or CLI upgrade can be re-checked against both canary sets with the control. The control must score near zero on both sets, or the set no longer tells the as-of date from the real one. As the real date moves on, a canary asked after an event stops discriminating, so canaries ask before deadlines.
