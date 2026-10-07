from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.envelope import Kind, Participant, Role, Source
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers.calendar import normalize_calendar_event

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"
CAPTURED_AT = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)
SWIM = (FIXTURES / "swim_lessons.ics").read_bytes()


def normalize_fixture(name: str):
    return normalize_calendar_event((FIXTURES / name).read_bytes(), captured_at=CAPTURED_AT)


def test_an_event_is_a_calendar_event_from_icloud_on_its_named_calendar():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.source == Source.ICLOUD_CALENDAR
    assert envelope.kind == Kind.CALENDAR_EVENT
    assert envelope.calendar_name == "Family"
    assert envelope.subject == "Swim lessons, Theo & June"


def test_episode_id_is_derived_from_the_uid_sequence_and_last_modified_time():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.native_id == "6F1C2A0E-swim-lessons@icloud.com"
    assert envelope.episode_id == "icloud_calendar_9d69b653f9a42b18"


def test_occurred_at_keeps_start_and_end_with_the_original_timezone_offset():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-10-17T09:00:00-05:00",
        "end": "2026-10-17T09:45:00-05:00",
    }


def test_participants_are_the_organizer_and_attendees():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.participants == [
        Participant(identifier="argus@example.com", name="Argus McNevans", role=Role.ORGANIZER),
        Participant(identifier="mara@example.com", name="Mara McNevans", role=Role.ATTENDEE),
        Participant(identifier="theo@example.com", name="McNevans, Theo", role=Role.ATTENDEE),
    ]


def test_body_has_the_status_location_and_description():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.body == (
        "Status: confirmed\n"
        "Location: Westside YMCA, 400 Elm St\n"
        "\n"
        "Bring goggles and towels.\n"
        "Pool B, lane 3."
    )


def test_a_repeating_event_says_how_it_repeats():
    weekly = SWIM.replace(b"STATUS:CONFIRMED", b"STATUS:CONFIRMED\r\nRRULE:FREQ=WEEKLY;COUNT=6")

    envelope = normalize_calendar_event(weekly, captured_at=CAPTURED_AT)

    assert envelope.body.startswith(
        "Status: confirmed\nLocation: Westside YMCA, 400 Elm St\nRepeats: FREQ=WEEKLY;COUNT=6\n\n"
    )


def test_a_line_folded_inside_a_multibyte_character_is_unfolded_before_decoding():
    folded = SWIM.replace(
        b"SUMMARY:Swim lessons\\, Theo & June", "SUMMARY:Swim lessons\\, Th\u00e9o & June".encode()
    )
    split_at = folded.index("\u00e9".encode()) + 1
    folded = folded[:split_at] + b"\r\n " + folded[split_at:]

    envelope = normalize_calendar_event(folded, captured_at=CAPTURED_AT)

    assert envelope.subject == "Swim lessons, Th\u00e9o & June"


def test_a_multi_valued_parameter_does_not_hide_the_attendee():
    delegated = SWIM.replace(
        b"ATTENDEE;CN=Mara McNevans;",
        b'ATTENDEE;DELEGATED-FROM="mailto:a@example.com","mailto:b@example.com";CN=Mara McNevans;',
    )

    envelope = normalize_calendar_event(delegated, captured_at=CAPTURED_AT)

    assert (
        Participant(identifier="mara@example.com", name="Mara McNevans", role=Role.ATTENDEE)
        in envelope.participants
    )


def test_every_version_of_an_event_shares_its_uid_as_the_thread():
    envelope = normalize_fixture("swim_lessons.ics")

    assert envelope.thread_ref == "6F1C2A0E-swim-lessons@icloud.com"


def a_later_version(raw: bytes, sequence: int, last_modified: str, *changes: tuple[bytes, bytes]):
    raw = raw.replace(b"SEQUENCE:0", f"SEQUENCE:{sequence}".encode())
    raw = raw.replace(b"LAST-MODIFIED:20261001T135900Z", f"LAST-MODIFIED:{last_modified}".encode())
    for old, new in changes:
        raw = raw.replace(old, new)
    return raw


def test_an_update_is_a_new_version_of_the_same_event():
    moved = a_later_version(
        SWIM, 1, "20261005T120000Z", (b"20261017T09", b"20261017T10"), (b"T094500", b"T104500")
    )

    original = normalize_calendar_event(SWIM, captured_at=CAPTURED_AT)
    update = normalize_calendar_event(moved, captured_at=CAPTURED_AT)

    assert update.episode_id != original.episode_id
    assert update.thread_ref == original.thread_ref
    assert update.occurred_at.start.isoformat() == "2026-10-17T10:00:00-05:00"


def test_a_cancellation_is_a_new_version_that_says_it_is_cancelled():
    cancelled = a_later_version(
        SWIM, 2, "20261008T120000Z", (b"STATUS:CONFIRMED", b"STATUS:CANCELLED")
    )

    original = normalize_calendar_event(SWIM, captured_at=CAPTURED_AT)
    cancellation = normalize_calendar_event(cancelled, captured_at=CAPTURED_AT)

    assert cancellation.episode_id != original.episode_id
    assert cancellation.body.startswith("Status: cancelled\n")


def test_a_reexport_with_a_new_dtstamp_is_the_same_version_with_the_same_content():
    reexported = SWIM.replace(b"DTSTAMP:20261001T140000Z", b"DTSTAMP:20261009T080000Z")

    first = normalize_calendar_event(SWIM, captured_at=CAPTURED_AT)
    second = normalize_calendar_event(reexported, captured_at=datetime(2026, 10, 9, tzinfo=UTC))

    assert second.episode_id == first.episode_id
    assert second.content_hash == first.content_hash


def test_a_bare_event_has_no_participants_an_empty_body_and_utc_times():
    envelope = normalize_fixture("night_shift.ics")

    assert envelope.participants == []
    assert envelope.calendar_name == "Mara \u2013 Hospital"
    assert envelope.body == ""
    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-10-13T00:00:00Z",
        "end": "2026-10-13T12:00:00Z",
    }


def test_an_all_day_event_spans_its_days_from_midnight_in_the_calendars_time_zone():
    all_day = SWIM.replace(
        b"DTSTART;TZID=America/Chicago:20261017T090000", b"DTSTART;VALUE=DATE:20261009"
    ).replace(b"DTEND;TZID=America/Chicago:20261017T094500", b"DTEND;VALUE=DATE:20261010")

    envelope = normalize_calendar_event(all_day, captured_at=CAPTURED_AT)

    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-10-09T00:00:00-05:00",
        "end": "2026-10-10T00:00:00-05:00",
    }


SWIM_EVENT = SWIM[SWIM.index(b"BEGIN:VEVENT") : SWIM.index(b"END:VCALENDAR")]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (SWIM.replace(b"UID:", b"X-UID:"), "no UID"),
        (SWIM.replace(b"DTSTART;", b"X-START;"), "no DTSTART"),
        (SWIM.replace(b"T090000", b"T9AM"), "unreadable DTSTART"),
        (SWIM.replace(b"20261017T090000", b"20261317T090000"), "unreadable DTSTART"),
        (SWIM.replace(b"America/Chicago:", b"Mars/Olympus:"), "unknown time zone"),
        (SWIM.replace(b"END:VCALENDAR", SWIM_EVENT + b"END:VCALENDAR"), "2 events"),
        (SWIM.replace(SWIM_EVENT, b""), "0 events"),
        (SWIM.replace(b"END:VCALENDAR\r\n", b""), "not a complete VCALENDAR"),
        (b"Swim lessons on Saturday at 9", "unreadable line"),
        (SWIM.replace(b"Theo & June", "Th\xe9o".encode("latin-1")), "not UTF-8"),
    ],
    ids=[
        "no-uid",
        "no-start",
        "bad-time",
        "bad-date",
        "unknown-zone",
        "two-events",
        "no-event",
        "unterminated",
        "not-icalendar",
        "not-utf8",
    ],
)
def test_a_calendar_that_is_not_exactly_one_readable_event_is_malformed(raw: bytes, reason: str):
    with pytest.raises(MalformedPayloadError, match=reason):
        normalize_calendar_event(raw, captured_at=CAPTURED_AT)
