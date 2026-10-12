# Personal Assistant

A personal assistant that remembers the world around its owner (people, meetings, mail, calendar, notes) and answers questions with attribution, in time order, and with awareness of what is still open.

## People

**Owner**:
The one person a PA instance serves. Their identity (names, email addresses) is instance configuration.
_Avoid_: User, me

**Person**:
A human in the owner's world, known by their addresses, names, and aliases, or only from being mentioned (a child, a doctor named in a note). The owner is a person too.
_Avoid_: Contact, user, entity

**Organization**:
A business or institution that sends mail, makes statements, or makes commitments, such as a clinic, a hotel, or a YMCA. A person can belong to one.
_Avoid_: Company, vendor, entity

## Capture and L0

**Source**:
A system the PA captures from, such as Gmail, iCloud Calendar, or the Obsidian vault.
_Avoid_: Feed, channel, integration

**Raw payload**:
An original item exactly as a source produced it (an email message, a calendar event, a note). Raw payloads are never modified.
_Avoid_: Original, blob

**Inbox**:
Where captured raw payloads wait to be ingested.
_Avoid_: Landing area, drop folder

**Ingest**:
Turning raw payloads in the inbox into episodes in L0.
_Avoid_: Import, load

**Episode**:
One captured item in L0: a raw payload together with its envelope. A changed item (an edited note, an updated calendar event) is a new episode, never an edit to an old one.
_Avoid_: Record, document, event, item

**Envelope**:
The normalized, source-independent description of an episode: who, when, what kind, which thread, and the normalized body. Versioned and reproducible from the raw payload.
_Avoid_: Metadata, header

**L0**:
The append-only store of episodes. Its raw payloads are the only source of truth; envelopes and every other layer are derived from them.
_Avoid_: Raw layer, data lake

**Quarantine**:
Where input that cannot become an episode is kept: malformed payloads, files in the inbox that aren't payloads or that ingest can't read (kept without their content), and payloads whose episode id already exists with different content.
_Avoid_: Dead letter, rejects

**Daily note**:
The owner's single Obsidian note for one day, with topics organized under headings. Finished, and captured, once the day is over.
_Avoid_: Journal, log

**Catalog**:
A derived, rebuildable index of the episodes in L0, for fast lookups by id, source, time, and participant.
_Avoid_: Database, manifest

## Memory

**Librarian**:
The batch process that reads episodes and derives claims from them into memory, using models that have no tools.
_Avoid_: Extractor, indexer, pipeline

**Claim**:
Anything derived from episodes into memory: a statement, a request or commitment, what a statement is about, or who someone is. Every claim points back to the episode it came from.
_Avoid_: Fact, knowledge

**Topic**:
A matter in the owner's life that unfolds over time and that they would ask about by name, such as a kitchen remodel, a wedding, or a knee injury. A statement can be about several topics. People and organizations are not topics.
_Avoid_: Subject, tag, project, thread

**Statement**:
Something someone said or wrote, as recorded in one episode, of a kind such as proposed, agreed, disagreed, asked, answered, committed, reported status, or decided. It is firsthand when the episode is the speaker's own words (their email, their note, their turn), and secondhand when the episode's author recounts what someone else said. Statements accumulate: a later statement never changes an earlier one.
_Avoid_: Fact, assertion, utterance

**Request**:
Someone asking someone else to do something, made in a statement and open until it is fulfilled, declined, withdrawn, or accepted. Accepting a request makes a commitment.
_Avoid_: Ask, task

**Commitment**:
A promise by someone to do something for someone, made in a statement and open until it is fulfilled, withdrawn, or superseded by a newer promise.
_Avoid_: Task, to-do, action item

**Calendar event**:
Something on the owner's calendar at a set time, such as a meeting or an appointment. It can be moved or cancelled; each change arrives as a new episode, and the latest shows where it stands now.
_Avoid_: Appointment, meeting, event

**Open loop**:
A request or commitment that is still open. It is overdue when its due date has passed, which depends on when you ask, not on anything recorded.
_Avoid_: Pending item, outstanding task

**Stance**:
Someone's position on a topic at a point in time, as their statements show it. It can change; the latest statements show where it stands now.
_Avoid_: Opinion, view

## Assistant

**Assistant**:
The PA as the owner meets it: Claude answering from the store, whether in chat, a scheduled run, or an eval run.
_Avoid_: Agent, bot

**Agent project**:
The Claude Code project directory that configures the assistant: persona, skills, tool and permission settings, hooks. Shared by both instances and free of instance values.
_Avoid_: Harness config, agent

**Session**:
One conversation with the assistant, from its first message to its last, whether started by the owner, a schedule, or an eval run.
_Avoid_: Chat, thread, conversation

**Exchange**:
One owner message in a session together with the assistant's reply to it. A captured exchange is an `assistant_chat` episode; the exchanges of one session share its thread.
_Avoid_: Message, transcript

**Turn**:
One speaker's contiguous words within an exchange or a meeting transcript. In an exchange, each turn is the owner's or the assistant's; only owner turns are evidence about the world.
_Avoid_: Utterance, message

## Evaluation

**Synthetic corpus**:
A fictional, frozen set of raw payloads used to evaluate the PA. It is a test fixture, not data.
_Avoid_: Test data, sample data

**Scale corpus**:
The synthetic corpus grown to a realistic size and mess: the original corpus unchanged, plus months of earlier history, new storylines, and the bulk mail a real inbox fills with. The original eval set's answers hold on it unchanged.
_Avoid_: Big corpus, stress corpus

**Storyline spec**:
The hand-written description of the synthetic corpus's people, storylines, and dated events. The ground truth for the eval set.
_Avoid_: Scenario, script

**Eval set**:
The fixed questions, with expected answers and required evidence episodes, that each milestone is scored against.
_Avoid_: Test suite, benchmark

**Eval configuration**:
What the assistant is given besides L0's files in an eval run: nothing (files alone), the claims graph with all of the PA MCP server's tools (the graph plus files), or the graph's `find`, `schema` and `query` tools only (query only).
_Avoid_: Mode, variant, arm

**Injection checks**:
The checks run against every eval build of the claims graph: no statement recorded in an adversarial email is the owner's, and no request or commitment owed by the owner is opened by one. A failure stops the run.

**As-of time**:
The moment an eval question is asked from; the assistant treats it as "now".
_Avoid_: Eval date, reference date

**Baseline**:
The assistant answering by plain file search over L0, with no derived layers. The first score that later milestones are compared against.
