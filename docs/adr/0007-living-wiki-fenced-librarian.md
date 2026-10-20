# L1 is a living wiki, kept by a fenced librarian

v0.12's claims graph lost to files alone ([ADR-0006](0006-remove-the-claims-graph.md)). Answers built from fragments extracted before any question existed lost to answers built by reading the episodes with the question in hand. So L1 becomes a Markdown wiki that maps into L0 instead of re-encoding it. It has pages for people, organizations, and topics (identity, current state, decisions, open loops) and a day log per day, and every line cites an episode. The assistant navigates with the wiki and answers from episodes.

The wiki is living: the librarian edits it in place, and a rebuild from L0 is for recovery only. That replaces the old principle that derived layers are drafts and a rebuild is the fix. The librarian is a headless Claude Code session with file tools, not toolless extraction, because keeping pages current means reading and editing them. Owner corrections arrive as chat turns, which are episodes, and the next run applies them.

The librarian holds private data, reads untrusted mail, and writes memory that persists. So it is fenced at its own sandbox's boundary, with no web access. Principle 7 now says that no component may combine private data, untrusted content, and external communication.

## The fences

- The librarian runs in its own Incus VM. L0 and the librarian project (its instructions, the wiki schema, and the templates) are mounted read-only, and the wiki is its only writable mount.
- Nothing in the writable mount is ever loaded as instructions or run as code. The wiki holds only Markdown content, which lint enforces, and its git directory lives on the host, which commits each run. Otherwise one injected email could rewrite the librarian's instructions for every later run, or plant a git hook that runs on the host. So the wiki is attached through settings, and it is never the session's working directory: there, Claude Code would load its `CLAUDE.md` files and skills, and even apply an untrusted `.claude/settings.json`.
- File tools only. Managed settings deny every other tool in every permission mode (the shell, web tools, and MCP, and also tools such as `RemoteTrigger` that reach out through the model endpoint), and auto memory is off. The network fence alone can't keep the web out: WebSearch runs behind the model endpoint, the one destination that must stay open, and a session with a shell could start a nested session or call the API with its own credentials.
- Egress only to the model endpoint, by address rules on a bridge of the librarian's own with DNS off, since a bridge's resolver forwards lookups and so is a way out. Auth is a setup token that can make model requests only.
- The assistant's direct web access ends in v0.13, before real mail reaches the store. From v0.14, web lookups go through a research worker that sees neither L0 nor the wiki.

## Considered Options

- **Keep or fix the claims graph.** Rejected in ADR-0006.
- **Pages created while observing new episodes.** Rejected: that phase has no view of the whole wiki, so "BGPT" and "Bridgestone GPT" would get two pages before anything could catch them. Consolidation creates pages, after checking aliases, addresses, and near-duplicate slugs.
- **The librarian's instructions and templates inside the wiki**, with a `CLAUDE.md` importing the schema. Rejected: the wiki is the librarian's writable mount, so an injection would persist in its own instructions.
- **A shell for the librarian**, for `rg` and `git`. Rejected for the reason under the fences; the host runs the deterministic steps instead.
- **Web access for the librarian, even search-only.** Rejected: it completes the lethal trifecta, and injected web text would persist in memory.
- **The chat assistant fixes the wiki immediately.** Rejected: it reads untrusted content, and its VM has no write path to the store by design.
- **`curation.md` as the source of truth for corrections.** Rejected: corrections are episodes (principle 1).
- **Routine rebuilds from L0.** Rejected: the wiki is living, so rebuilds are for recovery.
- **A container for the librarian.** Rejected: it shares the host kernel and would need its own deploy code, while the assistant VM's scripts carry over.

## Consequences

- A rebuild must stay possible. Replaying L0 through the librarian in batches is that rebuild, and the same replay builds each eval wiki, once per librarian version.
- Changing a template means the librarian migrates the pages, not a rebuild.
- The wiki is derived from untrusted content. The assistant treats it as a map that can be wrong and is never exhaustive, and cites only episodes. An adversarial episode may yield observations only.
- Corrections now carry authority, so the forged-exchange path ([ADR-0004](0004-inbox-directory-sets-source.md)) matters more. It stays accepted only while the store is synthetic, and it closes in v0.13.
- Principle 7 binds the assistant too. From v0.13 its VM reaches only the model endpoint, its web tools are denied, and its auto memory is off.
