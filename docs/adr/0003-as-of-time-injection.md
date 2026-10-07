# The agent is told "now" in an appended system prompt

The agent's "now" comes from one value: the question's as-of time in evals, the wall clock otherwise (`ClaudeAgent.answer(..., now=None)` falls back to its clock). Claude Code puts the real date in its own system prompt and that can't be turned off, so the injected time has to override it. We append a statement of the current time (weekday, date, time, UTC offset, and ISO form, plus "this overrides any other date you see") to Claude Code's system prompt with `--append-system-prompt`. A message before the question worked just as well on the canaries. We chose the system prompt because it keeps the user turn exactly what the owner asked. It also works in an interactive session, where nothing can be slipped in ahead of each message the owner types, and the agent doesn't credit the date to the owner ("the date you gave me"), as it did with the message. Neither method failed, so the fallback of running the agent under a faked system clock wasn't needed.

## The experiment

Five canaries over the fixture corpus (`evals/experiments/as_of_canaries.json`), each with an as-of time away from the real date (2026-10-06, a Tuesday): the day of the week, today's date, days left before a deadline, whether a deadline has passed, and whether a scheduled event has happened. The last three need L0 as well as the date. We ran `pa-eval run --agent claude --as-of-method <method> --judge model --judge-model sonnet` on 2026-10-06 with Claude Code 2.1.292, and counted the canaries judged correct:

| Method | haiku (2 runs) | sonnet (1 run) |
|---|---|---|
| `none` (control: no injection) | 0/10 | 0/5 |
| `system-prompt` | 10/10 | 5/5 |
| `preamble` (message before the question) | 10/10 | 5/5 |

With no injection, every answer used the real date ("Today is Tuesday", "3 days left", "not yet"). With either method, every answer used the as-of date, including the date arithmetic over L0. To repeat it: `pa-eval run --corpus evals/tests/fixtures/corpus --eval-set evals/experiments/as_of_canaries.json --agent claude --agent-model haiku --as-of-method {none,system-prompt,preamble} --judge model`.

## Consequences

- A first round, before the agent's role was appended to the system prompt, also scored both methods 4/4 on the date-only canaries. But haiku answered every L0 canary as a coding assistant ("I don't have access to your calendar") without searching. The agent project's CLAUDE.md was not enough to override Claude Code's coding-assistant framing, so `ClaudeAgent` always appends a short `ROLE` line to the system prompt pointing at CLAUDE.md. Keep it.
- `--as-of-method` stays on `pa-eval run` (default `system-prompt`) and the report records it, so a model or CLI upgrade can be re-checked against the canaries with the control.
