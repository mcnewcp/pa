---
name: claims-graph
description: How to answer from the claims graph, the PA MCP server's tools (`mcp__pa__*`). Use whenever those tools are available and a question is about people, organizations, topics, what someone said, requests and promises, or calendar events.
---

# The claims graph

The claims graph is an **index** over L0, built by the librarian: people and organizations,
topics, statements (who said what, when, with a quote checked against the episode), requests
and commitments with how each one closed, and calendar events with every version. Every node
and edge carries the ids of the episodes it came from. L0 stays the **evidence**: the graph
tells you where to look and what was said; the episodes are what you cite.

## Graph or files

- **Start with the graph** for who, what was said, what happened about a matter, what is
  still open, how people connect, and when things are scheduled. One call there replaces many
  greps through L0.
- **Go to the files** when you need more than a statement's quote (the full wording, the
  surrounding thread, details the extraction left out), and to check anything the answer
  rests on. A statement carries its episode's id and L0 path: open that file with Read.
- **Search the files** for what the graph leaves out: bulk mail that triage skipped
  (promotions, social notifications, most updates and newsletters), and anything that isn't a
  claim by someone, such as an attachment's details or a message's exact layout.
- When the graph comes back empty or thin for something the owner clearly expects to be
  there, search L0 before you answer.

## Which tool

1. **Names first.** `find` turns a name, nickname, alias, email address or id into the
   people, organizations and topics it matches. The tools that take `who` or `topic` resolve
   names the same way, and when a name matches more than one, they return the candidates
   instead of guessing: pick the right one and call again with its id.
2. **A domain query when one fits.** Every tool other than `schema` and `query` is a domain
   query, written for one kind of question (a topic's timeline, what someone said, open loops,
   calendar events). Read each tool's description and use the one that fits.
3. **`query` for the rest.** For paths, connections between people, counts, and anything no
   domain query covers: call `schema` first. It returns the live schema and every domain
   query's own Cypher with its rules. Adapt the nearest domain query rather than writing one
   from scratch, then run it with `query`. If `query` reports an error, a stop, or
   `truncated`, narrow the query and try again.

## No record

"Nothing matches" from `find` is a spelling miss until proven otherwise. Before you say there
is no record of someone or something, try `find` with another spelling, a nickname, a first or
last name alone, and an email address, and search L0. Say what you tried.

## Saying who said it

- A statement's speaker is the `SAID` edge into it. Name the speaker, and keep it straight
  from the people the statement mentions.
- **Secondhand** (`secondhand` is true): the episode's author recounts what someone else said.
  Say so, with both names: "Dave told you that Maria said the tile is in."
- **Told to the assistant** (`told_to_assistant` is true): the owner said it in a past chat with
  you. Say so: "You told me on Oct 2 that…".
- An open request or commitment has a due date but no "overdue" flag: judge overdue yourself,
  against now.

## Citing

Cite as CLAUDE.md says, `[ep:<episode_id>]`, using the episode ids the graph returns: a
statement's `RECORDED_IN` episode, and the episode ids every node and edge carries. Cite the
episodes, never the graph's own ids.
