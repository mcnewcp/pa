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

**Collector**:
The process that brings one source's new and changed items into the inbox as raw payloads, on a schedule and without the owner's help.
_Avoid_: Crawler, importer, sync

**Backfill**:
A one-time capture of a source's history from before the PA started, bounded by a window of time.
_Avoid_: Import, replay, history load

**Reconciliation**:
A collector's periodic comparison of everything a source holds within its window against what it has already captured: changed items land as new versions, and items gone from the source land as deletions.
_Avoid_: Snapshot, full sync, sweep

**Watermark**:
The time through which a source is known to be fully captured into L0. An absence ("nothing from Jay since Friday") is only as true as its source's watermark.
_Avoid_: Cursor, last sync, sync time

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
The owner's single Obsidian note for one day, with topics organized under headings. Finished, and captured, once the day is over. A correction captured later is a new version of it; the latest version by capture time supersedes the earlier ones.
_Avoid_: Journal, log

**Catalog**:
A derived, rebuildable index of the episodes in L0, for fast lookups by id, source, time, and participant.
_Avoid_: Database, manifest

## The owner's world

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
Something on the owner's calendar at a set time, such as a meeting or an appointment. It can be moved, cancelled, or deleted; each change arrives as a new episode, and the latest shows where it stands now.
_Avoid_: Appointment, meeting, event

**Open loop**:
A request or commitment that is still open: something owed by one party to another. It is overdue when its due date has passed, which depends on when you ask, not on anything recorded.
_Avoid_: Pending item, outstanding task

**Decision**:
Where a choice about a topic stands (proposed, made, or rejected), by whom, and when. A later decision on the same choice supersedes it.
_Avoid_: Resolution, ruling, outcome

**Stance**:
Someone's position on a topic at a point in time, as their statements show it. It can change; the latest statements show where it stands now.
_Avoid_: Opinion, view

## Memory

**Librarian**:
The process that keeps the wiki current, and the wiki's only writer. Each run observes new episodes into day logs and then consolidates them into pages; dreaming is its periodic review of the whole wiki.
_Avoid_: Extractor, indexer, pipeline

**Wiki**:
L1: the Markdown pages and day logs the librarian keeps over L0, recording who's who, where each topic stands, decisions, and open loops. It is a map into L0, never evidence itself; every line cites the episode it came from.
_Avoid_: Knowledge base, memory, vault, notes

**Page**:
One wiki file about one person, organization, or topic: where things stand, its decisions and open loops, and notes.
_Avoid_: Article, entry, card

**Day log**:
The wiki's file for one day, with an observation for each relevant episode of that day.
_Avoid_: Day note, daily note, journal

**Observation**:
One line in a day log saying what one episode shows, linking the people, organizations, and topics involved and citing the episode.
_Avoid_: Fact, note, entry

**Consolidation**:
The second phase of every librarian run: folding that run's new observations into pages, and creating pages for the people, organizations, and topics that have come up often enough to need one.
_Avoid_: Dreaming, compaction

**Replay**:
Rebuilding a wiki from scratch by running L0 through the librarian a week of occurrence at a time, each week one run told its end as now. It is the rebuild for recovery, and it builds each eval wiki.
_Avoid_: Rebuild run, backfill

**Dreaming**:
A separate, periodic librarian run over the whole wiki. It judges candidate problems (duplicate pages, contradictions, stale loops), fixes what a re-read of L0 settles, and turns the rest into proposals for the owner.
_Avoid_: Consolidation, cleanup

**Correction**:
An owner turn saying that something the PA remembers is wrong, such as two pages for one person or a loop that has already closed. Like every episode it is never an edit; the librarian applies it on its next run.
_Avoid_: Fix, override

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
What the assistant is given besides L0's files in an eval run: nothing (files alone), or the wiki (files plus wiki).
_Avoid_: Mode, variant, arm

**Eval wiki**:
The wiki a files-plus-wiki run gives the assistant: a replay of a frozen corpus, built once per librarian version and kept in the evals tree with its run log.
_Avoid_: Test wiki, fixture wiki

**Cut**:
A frozen copy of the store, taken now, that a real-data eval set is asked against: every episode captured by then, the live wiki at its last commit, and the status file. It lives under the data root, never in the repository, so the set's answers never change as L0 grows.
_Avoid_: Snapshot (that's a backup's), frozen corpus (that's synthetic)

**As-of time**:
The moment an eval question is asked from; the assistant treats it as "now".
_Avoid_: Eval date, reference date

**Baseline**:
The assistant answering by plain file search over L0, with no derived layers. The first score that later milestones are compared against.
