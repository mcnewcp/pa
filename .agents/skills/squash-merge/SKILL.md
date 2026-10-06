---
name: squash-merge
description: Squash merge a PR with a Conventional Commits message written for future agents.
argument-hint: "PR number"
disable-model-invocation: true
---

The squash commit is the only trace of this PR left on `main`. Future agents read it through `git log` and `git blame` to learn why the code is the way it is, so write it as **lasting context**: dense, no padding.

1. Gather the sources: `gh pr view <n> --comments`, the branch commits (`gh pr view <n> --json commits`), and the issue or spec the PR closes (via `docs/agents/issue-tracker.md`). The completion criterion is that you can say in one sentence what changed for the system as a whole, and why.
2. Write the subject: `type(scope): summary` in the imperative, at most 72 characters. Choose the type that fits the PR's net effect, not its last commit, and add `!` for a breaking change.
3. Write the body as a few short lines, wrapped at 72 characters:
   - **Why**: the problem or goal, in `GLOSSARY.md` terms.
   - **What**: the behaviour change, at the level of a module or interface.
   - **Decisions**: any non-obvious choice or rejected alternative that a future agent would otherwise re-litigate. Include this only when one exists.
   - `Closes #<issue>` for each issue it resolves, plus a `BREAKING CHANGE:` footer when one applies.

   The diff and the PR keep the detail, so the body names only what they can't show at a glance.
4. Show the user the subject and body, and wait for approval.
5. Merge: `gh pr merge <n> --squash --subject "<subject>" --body "<body>"`. Then confirm with `git log origin/main -1` after a fetch.
