Commits use Conventional Commits (`type(scope): summary`).

## Real content

The real data root (`~/.local/share/pa-home`) holds the owner's mail, calendars, notes, and chats. This repository is public, and a development session holds a shell, credentials, web access, and push rights, so a session sees the real store only as **structure**: names, kinds, counts, and sizes.

- **Run the real pipeline** when a ticket needs it, and read only its counts, exit codes, statuses, quarantine reasons, and lint warning counts.
- **Real content stays unprinted**: payloads, envelopes, wiki pages and lint findings, the live wiki's git log, and real eval results. That covers every command: `cat`, `grep`, `git log`, a Python one-liner. The repository's `.claude/settings.json` denies the file tools there as a backstop, not as the rule.
- **Debug a real payload through `pa inspect PATH`**, which prints its structure. Reproduce the shape as a synthetic fixture, then fix it test-first. Fixtures, tests, commits, and issue comments hold synthetic data only.
- **Private material for the owner's review** (eval questions, failing answers, a verdict's details) goes in files under the data root. The GitHub issue gets only a note that it's ready.

The synthetic data root (`~/.local/share/pa-home-synthetic`) and the corpora are fictional; read them freely.

## Agent skills

### Issue tracker

Issues are tracked as GitHub Issues on `mcnewcp/pa`, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default label names: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
