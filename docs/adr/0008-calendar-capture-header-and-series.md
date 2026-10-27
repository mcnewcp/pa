# A captured calendar resource is one episode per series, behind a capture header

The frozen corpus's `.ics` payloads are shaped like a calendar export: one VEVENT each, with the calendar's name in `X-WR-CALNAME`. Real iCloud resources read over CalDAV differ in three ways:

- A recurring series with any changed or removed occurrence is one resource. It holds the master VEVENT plus an override VEVENT for each changed occurrence (`RECURRENCE-ID`), and the normalizer quarantines it.
- A resource doesn't carry its calendar's name. That's a property of the CalDAV collection.
- A deleted event leaves no payload at all.

So the collector lands each resource exactly as CalDAV served it, behind a capture header like ADR-0002's for daily notes: `Name: value` lines and a blank line. Two fields are required. `Calendar` is the collection's display name. `Captured-At` is when the collector read this version, and it becomes the envelope's `captured_at`, as it does for notes. When a resource disappears from its calendar, the collector lands its last-known bytes with a `Deleted-At` field added.

A resource is one episode: the series together with its overrides. Its version component covers every VEVENT in it, so a change to any occurrence is a new episode, and the latest version shows where the whole series stands. It covers each VEVENT's content apart from `DTSTAMP`, not just `SEQUENCE` and `LAST-MODIFIED`: the spike (#85, [findings](../research/calendar-structure-spike.md)) showed iCloud ending a series with an `UNTIL` and bumping neither. A deletion is an episode too, and its id differs from every version's. The normalized body describes the recurrence in words and lists the occurrences in a window around `Captured-At`, with overrides and exclusions applied. The assistant reads dates instead of doing RRULE arithmetic, and renormalize reproduces the same body because the window's anchor is in the payload.

## An unchanged series landed again keeps its first list (#96)

The version leaves `Captured-At` out, so the same resource landed again later has the same episode id and a list from another window. That happens routinely: the nightly reconciliation, a re-listing after a sync token expires, and a backfill that overlaps live capture all land unchanged resources. Ingest would quarantine each as a conflict, since the id is the same and the body isn't.

So a captured resource's episode id is taken to pin its content. The version digests everything the envelope says apart from the list's anchor, so the same id means the same version, and ingest collapses it as unchanged. The version that landed first, with its list, stands until the series really changes. A header-less export's version is only `SEQUENCE` and `LAST-MODIFIED`, so a difference under its id is still a conflict.

The window stays anchored on `Captured-At`, as #80 asks. Anchoring it on something in the content would avoid the collapse rule, but an unchanged series' content says nothing about the present. A weekly class created a year ago and backfilled today would list last year's classes.

The collector reads changes through CalDAV's sync tokens every 15 minutes. Each night it also reconciles everything each calendar holds against what it has captured, so a deletion that a sync report missed still lands.

## Considered Options

- **One episode per VEVENT, with overrides split into payloads of their own.** Rejected. The collector would rewrite what the source served. An override alone lacks the series it changes, and a removed occurrence (`EXDATE`) changes only the master anyway.
- **Write the calendar's name into the VCALENDAR as `X-WR-CALNAME`.** Rejected for ADR-0002's reason: it modifies the raw payload.
- **Expand each occurrence into its own episode.** Rejected. Episodes are captured items, and occurrences are derived from them, so they belong in the envelope.
- **Keep recurrence as RRULE text in the body,** as today. Rejected: the assistant would have to work out the occurrences itself, and models get that wrong.
- **Represent a deletion as a synthesized `STATUS:CANCELLED` event.** Rejected. That fabricates a payload the source never produced, and a cancelled event isn't a deleted one.
- **Detect deletions only from sync reports.** Rejected. Sync tokens expire, and a missed report would lose a deletion for good.
- **Anchor the occurrence window on the content,** such as `LAST-MODIFIED` or the series' own dates, so a re-capture has the same body. Rejected (#96). A long-unchanged series would list occurrences from around when it was last edited, which may be years back, from its first capture on.
- **Put the capture time, or a period of it, into the version,** so a re-capture lands a fresh list. Rejected (#96). Every re-capture, or every period, would be a new episode that says nothing changed, and the librarian would read each one as a change. Story 61 has unchanged resources collapse.
- **Have the collector never land an unchanged resource.** Rejected as the guard (#96). Reconciliation, expired sync tokens and overlapping backfills each land unchanged resources, and ingest must take them whatever a collector remembers.

## Consequences

- The normalizer accepts both shapes. A payload without a header and with one VEVENT normalizes exactly as before, with the same episode id, so the frozen corpus, its eval sets, and its eval wikis hold.
- Versions of a series are ordered by `captured_at`, then episode id, as notes are. A deletion is the last version: its `captured_at` is its `Deleted-At`.
- The version also covers the calendar's name, since an event moved to another calendar is a new resource there with the same UID. It leaves out alarms (`VALARM`) as well as `DTSTAMP` (#93): an alarm is the owner's reminder, never shown in the envelope, and a client may mark one `ACKNOWLEDGED` each time it goes off.
- A series' `occurred_at` starts at its first occurrence. A series whose rule has a `COUNT` or `UNTIL`, or that has only `RDATE`s, ends when its last occurrence ends (#96). Any other series has no end. Nothing in it depends on `Captured-At`. A windowed replay's first window takes the calendar events still going at its start, so a series that began before a backfill's window is offered and one that ended before it isn't (#93).
- The occurrence window is a normalizer parameter: two months before `Captured-At` to six months after. Changing it means a renormalize. Each occurrence is listed in the event's own time zone with its UTC offset, so a daylight-saving change shows. A moved occurrence says where it moved from. An occurrence removed by `EXDATE` or cancelled by its override isn't listed. A deletion lists nothing.
- **A long-unchanged series' list goes stale.** It covers the window around the capture that first landed this version, and an unchanged re-capture doesn't refresh it. The body names its window, so the assistant can see when the list runs out. Past that, the recurrence in words still says how the series repeats, and the next real change lands a new version with a fresh list. A weekly class untouched for a year lists dates only up to six months after it was first captured.
- Collectors can ask whether a resource has an occurrence on or after a moment (`occurs_on_or_after`), with overrides and exclusions applied. A backfill uses it to decide which series fall in its window (#97).
- v0.13 starts with a spike that reads the owner's real calendars for their structure only, never their content. It checks:
  - how many VEVENTs a resource holds;
  - whether every change bumps `SEQUENCE` or `LAST-MODIFIED`;
  - how shared calendars appear;
  - whether a Birthdays calendar is served at all.

  If a change can arrive without a bump, the version component must cover the content as well, so that the change isn't quarantined as a conflicting duplicate.

  The spike (#85) found that one can: "all future events" ends the old series with an `UNTIL`, with no bump, and starts a new series with its own UID in a new resource. So the version covers the content, and a split series is two episodes. No Birthdays calendar is served.
