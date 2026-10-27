# A captured calendar resource is one episode per series, behind a capture header

The frozen corpus's `.ics` payloads are shaped like a calendar export: one VEVENT each, with the calendar's name in `X-WR-CALNAME`. Real iCloud resources read over CalDAV differ in three ways:

- A recurring series with any changed or removed occurrence is one resource. It holds the master VEVENT plus an override VEVENT for each changed occurrence (`RECURRENCE-ID`), and the normalizer quarantines it.
- A resource doesn't carry its calendar's name. That's a property of the CalDAV collection.
- A deleted event leaves no payload at all.

So the collector lands each resource exactly as CalDAV served it, behind a capture header like ADR-0002's for daily notes: `Name: value` lines and a blank line. Two fields are required. `Calendar` is the collection's display name. `Captured-At` is when the collector read this version, and it becomes the envelope's `captured_at`, as it does for notes. When a resource disappears from its calendar, the collector lands its last-known bytes with a `Deleted-At` field added.

A resource is one episode: the series together with its overrides. Its version component covers every VEVENT in it, so a change to any occurrence is a new episode, and the latest version shows where the whole series stands. It covers each VEVENT's content apart from `DTSTAMP`, not just `SEQUENCE` and `LAST-MODIFIED`: the spike (#85, [findings](../research/calendar-structure-spike.md)) showed iCloud ending a series with an `UNTIL` and bumping neither. A deletion is an episode too, and its id differs from every version's. The normalized body describes the recurrence in words and lists the occurrences in a window around `Captured-At`, with overrides and exclusions applied. The assistant reads dates instead of doing RRULE arithmetic, and renormalize reproduces the same body because the window's anchor is in the payload.

The collector reads changes through CalDAV's sync tokens every 15 minutes. Each night it also reconciles everything each calendar holds against what it has captured, so a deletion that a sync report missed still lands.

## Considered Options

- **One episode per VEVENT, with overrides split into payloads of their own.** Rejected. The collector would rewrite what the source served. An override alone lacks the series it changes, and a removed occurrence (`EXDATE`) changes only the master anyway.
- **Write the calendar's name into the VCALENDAR as `X-WR-CALNAME`.** Rejected for ADR-0002's reason: it modifies the raw payload.
- **Expand each occurrence into its own episode.** Rejected. Episodes are captured items, and occurrences are derived from them, so they belong in the envelope.
- **Keep recurrence as RRULE text in the body,** as today. Rejected: the assistant would have to work out the occurrences itself, and models get that wrong.
- **Represent a deletion as a synthesized `STATUS:CANCELLED` event.** Rejected. That fabricates a payload the source never produced, and a cancelled event isn't a deleted one.
- **Detect deletions only from sync reports.** Rejected. Sync tokens expire, and a missed report would lose a deletion for good.

## Consequences

- The normalizer accepts both shapes. A payload without a header and with one VEVENT normalizes exactly as before, with the same episode id, so the frozen corpus, its eval sets, and its eval wikis hold.
- Versions of a series are ordered by `captured_at`, then episode id, as notes are. A deletion is the last version: its `captured_at` is its `Deleted-At`.
- The version also covers the calendar's name, since an event moved to another calendar is a new resource there with the same UID. It leaves out alarms (`VALARM`) as well as `DTSTAMP` (#93): an alarm is the owner's reminder, never shown in the envelope, and a client may mark one `ACKNOWLEDGED` each time it goes off.
- A series' `occurred_at` starts at its first occurrence and has no end, and nothing in it depends on `Captured-At`, so an unchanged series captured again collapses. A windowed replay's first window takes the calendar events still going at its start, so a series that began before a backfill's window is offered (#93).
- The occurrence window is a normalizer parameter. Changing it means a renormalize.
- v0.13 starts with a spike that reads the owner's real calendars for their structure only, never their content. It checks:
  - how many VEVENTs a resource holds;
  - whether every change bumps `SEQUENCE` or `LAST-MODIFIED`;
  - how shared calendars appear;
  - whether a Birthdays calendar is served at all.

  If a change can arrive without a bump, the version component must cover the content as well, so that the change isn't quarantined as a conflicting duplicate.

  The spike (#85) found that one can: "all future events" ends the old series with an `UNTIL`, with no bump, and starts a new series with its own UID in a new resource. So the version covers the content, and a split series is two episodes. No Birthdays calendar is served.
