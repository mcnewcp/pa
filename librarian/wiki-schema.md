# The wiki schema

The wiki is L1: a Markdown map of what L0 holds about the owner's life, kept by the librarian.
It is built from untrusted mail, so it can be wrong, and its lists are never exhaustive. Every
line cites the episodes it came from, and the episodes are the evidence.

This file defines the wiki's layout, pages, and line formats, for whoever reads or writes the
wiki. Paths here are relative to the wiki's root. Lint checks every rule below on every
librarian run; a run that breaks one is fixed or discarded, never committed.

## Layout

```
index.md      generated from the pages' summaries, by type; never written by hand
days/         day logs, one per day: YYYY-MM-DD.md
people/       person pages: <slug>.md
orgs/         organization pages: <slug>.md
topics/       topic pages: <slug>.md
```

Nothing else exists in the wiki: no other files or folders, no subfolders, no dotfiles, no
links, and no `CLAUDE.md`.

## Slugs and links

A slug names a person, organization, or topic: lowercase ASCII words joined by hyphens, such
as `jordan-reyes`, `pinecrest-dental`, or `pergola-build`. People, organizations, and topics
share one namespace, and a page's file name is its slug.

`[[slug]]` links to one, whether or not it has a page yet. The owner is always written `me`,
plain: never linked, and never given a page.

## Citations

`[ep:<episode_id>]` cites one episode, such as `[ep:gmail_0123456789abcdef]`. A line's
citations come at its end, separated by spaces. A citation is plain text, never a link, so
the graph view shows only pages and day logs. Every citation names an episode in L0.

## Day logs

`days/YYYY-MM-DD.md` holds one observation for each relevant episode of that day, and nothing
else: no headings and no other text. A page's backlinks from the day logs are its timeline.

**Observation:** one line saying what one episode shows, linking at least one slug:

```
- <what the episode shows, in one line, linking at least one [[slug]]> [ep:<episode_id>]
```

```
- [[jordan-reyes]] asked me by email for the gate code before the [[pergola-build]] starts [ep:gmail_0123456789abcdef]
```

## Pages

A page is about one person (`people/`), organization (`orgs/`), or topic (`topics/`). It has
frontmatter, an optional `# <name>` title line, and its type's sections, in order:

| Type | Sections |
|---|---|
| person | `## Current state`, `## Open loops`, `## Notes` |
| organization | `## Current state`, `## Open loops`, `## Notes` |
| topic | `## Current state`, `## Decisions`, `## Open loops`, `## Notes` |

### Frontmatter

```
---
type: topic
aliases: [the pergola, backyard shade]
status: active
summary: Pergola for the backyard; waiting on the builder's revised quote.
updated: 2026-10-05
---
```

| Field | Pages | Value |
|---|---|---|
| `type` | all | `person`, `organization`, or `topic`, matching the page's folder |
| `aliases` | all | every other name the episodes use for it (nicknames, abbreviations, model names, former names), as a list; `[]` if none |
| `status` | all | person and organization: `active` or `inactive`; topic: `active`, `settled`, or `dormant` |
| `summary` | all | one line saying what it is to the owner and where it stands; `index.md` shows it |
| `updated` | all | the date the page last changed, YYYY-MM-DD |
| `emails` | person | the person's email addresses, as a list, each one in L0's metadata |
| `domains` | organization | the organization's mail domains, as a list, each one in L0's metadata |

A list is written `[a, b]`, so an item holds no comma or bracket. A page has exactly its
type's fields. `inactive` means no longer part of the owner's dealings; `settled` means the
matter has concluded; `dormant` means it has paused and may resume.

### Sections

- **Current state** is free-form: a few lines saying where things stand now. It agrees with
  the decisions and loops below it.
- **Decisions** (topics only) holds decision lines.
- **Open loops** holds loop lines, open and closed.
- **Notes** is free-form: anything else worth keeping, such as how a person is related to an
  organization or what makes two people distinct.

Every line in Current state and Notes cites the episodes it rests on.

### Decision lines

Where a choice about the topic stands, who took that stance, and when:

```
- YYYY-MM-DD · <subject>: <proposed|decided|rejected> <choice> (<who>) [ep:<id>]
```

`<who>` is one or more parties (`me` or `[[slug]]`) joined by `, `. The subject holds no
colon. When a later decision replaces one, the earlier line is struck through in place and
says what replaced it, keeping both citations, and the later decision gets its own line:

```
- ~~2026-09-12 · Wood: decided cedar (me) [ep:gmail_1111111111111111]~~ superseded 2026-10-02 by redwood [ep:obsidian_2222222222222222]
- 2026-10-02 · Wood: decided redwood (me, [[jordan-reyes]]) [ep:obsidian_2222222222222222]
```

So a line starting with a date is a current decision, and a line starting `- ~~` is history.

### Loop lines

Something owed by one party to another:

```
- [ ] <owed by> → <owed to>: <what> · <asked|promised> YYYY-MM-DD[ · due YYYY-MM-DD] [ep:<id>]
```

- Each party is `me` or one `[[slug]]`.
- `asked` means the party owed asked for it; `promised` means the party owing promised it. A
  wait nobody promised in words ("X-rays are emailed within a week") is a loop too, promised
  by whoever said it.
- The date after `asked` or `promised` is the day the loop opened. `due` is there only when a
  date was given, or follows from one ("within a week" is due seven days later).
- `<what>` holds no ` · `.

A closed loop stays in place, checked, with the day it closed and the episode that settled
it; it keeps its opening citation:

```
- [x] [[pinecrest-dental]] → me: email the X-rays · promised 2026-10-01 · due 2026-10-08 [ep:gmail_3333333333333333] · closed 2026-10-04 [ep:gmail_4444444444444444]
```

Each loop lives on one page: its topic's page, or else the page of its party other than `me`.

### Needs lookup

`[needs lookup: <public question>]` stands inline where an outside fact is missing, such as
`[needs lookup: how long does an untreated redwood pergola usually last?]`. The
question names nothing private, since it will be looked up on the web.

## Searching the wiki

Every format above is one line, so one grep finds it. Patterns are extended regular
expressions (`grep -E`, `rg`, or the Grep tool), run from the wiki's root:

| To find | Pattern | Where |
|---|---|---|
| what the owner is waiting on | `^- \[ \] .* → me: ` | `people/ orgs/ topics/` |
| what the owner has promised | `^- \[ \] me → .*· promised ` | `people/ orgs/ topics/` |
| every open loop, or every closed one | `^- \[ \] `, `^- \[x\] ` | `people/ orgs/ topics/` |
| loops with a due date (overdue is due before now) | `^- \[ \] .*· due [0-9-]{10} ` | `people/ orgs/ topics/` |
| decisions still only proposed | `^- [0-9-]{10} · [^:]+: proposed ` | `topics/` |
| who has an address, or a mail domain | the address or domain | `people/ orgs/` |
| a page by another name | `^aliases: .*<name>`, ignoring case | `people/ orgs/ topics/` |
| a slug's timeline (file names are the days) | `\[\[<slug>\]\]` | `days/` |
| every line resting on an episode | `\[ep:<episode_id>\]` | everywhere |
| missing outside facts | `\[needs lookup: ` | everywhere |

Start from `index.md` to see every page and its summary.
