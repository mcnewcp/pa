# Calendar structure spike (#85)

Run on 2026-10-27 on the devbox, against the owner's iCloud account over CalDAV (`caldav.icloud.com`), with an app-specific password. The spike (`python -m pa_home.calendar_spike`) fetched each calendar into a scratch directory under the data root and printed only counts and shapes. The development session saw nothing else: no summary, time, UID, href or other value. The owner made and changed a throwaway recurring event, titled `PA spike test`, while the spike took a snapshot after each step. Every fetched resource was deleted afterwards, along with the scratch directory.

The spike's tool is gone from the code; it lived in commit 0151b4c: `git show 0151b4c:adapters/home/src/pa_home/calendar_spike.py` (its tests: `adapters/home/tests/test_calendar_spike.py`). The CalDAV client it used, `collectors/calendar_server.py`, stays: the calendar collector reads iCloud through it.

## Results

| # | Question | Answer |
|---|---|---|
| 1 | Which calendars the account serves | **Home, Work and Family Calendar** (VEVENT), and **Reminders ⚠️** (VTODO). All four are writable |
| 2 | How the family calendar appears | As **`shared-owner`**: the owner owns it and shares it out. It reads like any other calendar |
| 3 | Whether a Birthdays calendar is served | **No.** It stays out of scope, as #80 planned |
| 4 | Whether every change bumps `SEQUENCE` or `LAST-MODIFIED` | **No.** "All future events" ends the old series with an `UNTIL` and bumps neither. **ADR-0008's version component must cover the content** |
| 5 | Whether a series with changed occurrences is one resource | **Yes.** A master and its overrides share one resource, with no orphan overrides |
| 6 | How sync tokens and etags behave | Every resource had an etag, and new bytes always came with a new etag. Sync reports agreed with listings taken at the same moment. A 90-minute-old token was still accepted and reported deletions |

## The calendars

Counts from the baseline snapshot:

| Calendar | Resources | Single events | Series | Series with overrides | EXDATE | RDATE | VTIMEZONE | Alarms |
|---|---|---|---|---|---|---|---|---|
| Home | 26 | 23 | 3 | 0 | 0 | 0 | 22 | 18 |
| Work | 51 | 51 | 0 | 0 | 0 | 0 | 49 | 40 |
| Family Calendar | 1,123 | 1,095 | 23 | 5 | 15 | 0 | 778 | 383 |

- **Events per resource.** It's one event per resource, except for the 5 series with overrides. Those hold 2 events (twice), 3 events (twice), and 5 events (once).
- **Version properties.** Every event has `SEQUENCE`. `LAST-MODIFIED` is missing from 1 event in Home and 12 in Family Calendar.
- **Calendar names.** No resource carries `X-WR-CALNAME`, so the calendar's name has to come from the collection, as ADR-0008 assumed.
- **Reminders ⚠️** holds 2 VTODOs and no events. It's the pre-iOS 13 list: reminders made since then aren't served over CalDAV. So reading the owner's reminders would need another route. That's a later source, not v0.13.

## The test event

| Step | What the owner did | What the resource did |
|---|---|---|
| 1 | Created a weekly series, then moved it from Family Calendar to Home | Removed from Family Calendar, and added in Home as a new resource. The series was unchanged. `SEQUENCE` stayed the same, `LAST-MODIFIED` moved, and Home added an alarm |
| 2 | Moved the whole series ("all future events") | **The series split.** The old resource stayed, its `RRULE` gained an `UNTIL`, and **neither `SEQUENCE` nor `LAST-MODIFIED` changed**. A new resource holds a new series from the moved occurrence on, with a **different UID** |
| 3 | Changed one occurrence | Same resource, new etag. An override (`RECURRENCE-ID`) was added with its own `SEQUENCE` and `LAST-MODIFIED`. The master was unchanged |
| 4 | Deleted one occurrence | Same resource, new etag. An `EXDATE` was added to the master. `LAST-MODIFIED` moved and `SEQUENCE` didn't |
| 5 | Deleted the event | Each resource was removed. Both the sync report and the listing showed it, one series at a time |

## Sync tokens and etags

- **Tokens.** Every collection gave a sync token, which changed with each change and stayed the same otherwise.
- **Tokens and deletions.** The baseline token was about 90 minutes old by the end. iCloud still accepted it, and its report listed both deleted series as removed, even though neither was in the baseline listing. So iCloud remembers deletions for at least that long.
- **When reports disagree.** A sync report disagreed with a listing only when the two were taken at different moments. Each disagreement was the step-1 move, seen half-way.
- **Etags.** No bytes changed under an unchanged etag, and no fetch returned an etag different from the listing's.
- **Wrong password.** It gives HTTP 401.

## What the client needed

- **`calendar-multiget` hrefs must be paths.** iCloud answers a full URL with 404 for every resource, and the first snapshot listed every calendar as empty. A local Radicale server had accepted both forms. `CalDavServer` now sends paths, and a test runs it against a stand-in that answers as iCloud does.
- The `caldav` library is used only to authenticate and to find the calendar home, which iCloud serves from a per-account host. Its logs and its request dump are off, since the dump would print the `Authorization` header.

## What this changes

- **ADR-0008** is amended. A headered resource's version component covers each VEVENT's content apart from `DTSTAMP`, not only `SEQUENCE` and `LAST-MODIFIED`.
- **A split series** is two episodes. The old series gets a new version that ends with its `UNTIL`, and the new series starts as a new resource with its own UID. The occurrence lists show where each one stands, so the assistant reads two series with dates rather than one series that moved. Linking them would take a heuristic; none is planned.
- **Moving an event between calendars** shows up as a deletion in one calendar and a new resource in the other. Under ADR-0008 that's a deletion episode plus a new episode.
- **Alarms** are part of a resource. Under the amended rule, changing one is a new version. That's a small cost; leaving `VALARM` out of the version is an option for #93.
- **Birthdays** isn't served, so it stays out of scope. Reminders stays out of scope too.
