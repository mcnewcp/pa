# Personal Assistant

A personal assistant that remembers the world around its owner (people, meetings, mail, calendar, notes) and answers questions with attribution, in time order, and with awareness of what is still open.

## People

**Owner**:
The one person a PA instance serves. Their identity (names, email addresses) is instance configuration.
_Avoid_: User, me

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
Where input that cannot become an episode is kept: malformed payloads, and payloads whose episode id already exists with different content.
_Avoid_: Dead letter, rejects

**Daily note**:
The owner's single Obsidian note for one day, with topics organized under headings. Finished, and captured, once the day is over.
_Avoid_: Journal, log

**Catalog**:
A derived, rebuildable index of the episodes in L0, for fast lookups by id, source, time, and participant.
_Avoid_: Database, manifest

## Evaluation

**Synthetic corpus**:
A fictional, frozen set of raw payloads used to evaluate the PA. It is a test fixture, not data.
_Avoid_: Test data, sample data

**Storyline spec**:
The hand-written description of the synthetic corpus's people, storylines, and dated events. The ground truth for the eval set.
_Avoid_: Scenario, script

**Eval set**:
The fixed questions, with expected answers and required evidence episodes, that each milestone is scored against.
_Avoid_: Test suite, benchmark

**As-of time**:
The moment an eval question is asked from; the agent treats it as "now".
_Avoid_: Eval date, reference date

**Baseline**:
The agent answering by plain file search over L0, with no derived layers. The first score that later milestones are compared against.
