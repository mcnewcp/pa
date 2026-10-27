"""Calendar resources as a CalDAV collector lands them (ADR-0008), through ingest.

Each resource is the bytes a calendar server served, behind a capture header naming its
calendar and when it was read. The fixtures are synthetic, shaped like what the calendar
structure spike (#85) found on iCloud: a VTIMEZONE and an alarm in most resources, a series
with its overrides in one resource, a removed occurrence as an EXDATE on the master, and a
series moved "for all future events" split into two resources with different UIDs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pa_home.cli import main

CALENDAR = "Moonwhistle Family"
CAPTURED_AT = "2026-10-20T08:15:00-05:00"

TIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:America/Chicago",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:-0600",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
    "DTSTART:20070311T020000",
    "TZNAME:CDT",
    "TZOFFSETTO:-0500",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:-0500",
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
    "DTSTART:20071104T020000",
    "TZNAME:CST",
    "TZOFFSETTO:-0600",
    "END:STANDARD",
    "END:VTIMEZONE",
]

ALARM = [
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    "DESCRIPTION:Reminder",
    "TRIGGER:-PT30M",
    "UID:alarm-0001",
    "END:VALARM",
]


def dentist(
    *,
    sequence: int = 0,
    last_modified: str = "20261001T135900Z",
    dtstamp: str = "20261020T131500Z",
    start: str = "20261028T150000",
    description: str = "Bring the referral form.",
) -> list[str]:
    """A single event, as most resources are."""
    return [
        "BEGIN:VEVENT",
        "UID:dentist-0001@example.com",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{dtstamp}",
        f"LAST-MODIFIED:{last_modified}",
        "CREATED:20261001T135900Z",
        f"DTSTART;TZID=America/Chicago:{start}",
        "DTEND;TZID=America/Chicago:20261028T160000",
        "SUMMARY:Dentist\\, Wren",
        "LOCATION:Bramble Dental",
        f"DESCRIPTION:{description}",
        "STATUS:CONFIRMED",
        *ALARM,
        "END:VEVENT",
    ]


POTTERY_UID = "pottery-0001@example.com"


def pottery(
    *,
    rule: str = "FREQ=WEEKLY;BYDAY=TU",
    last_modified: str = "20250901T120000Z",
    dtstamp: str = "20261020T131500Z",
    exdates: tuple[str, ...] = ("20261013T183000",),
    alarm: list[str] = ALARM,
) -> list[str]:
    """A weekly class that started a year before the capture: the series master."""
    return [
        "BEGIN:VEVENT",
        f"UID:{POTTERY_UID}",
        "SEQUENCE:0",
        f"DTSTAMP:{dtstamp}",
        f"LAST-MODIFIED:{last_modified}",
        "DTSTART;TZID=America/Chicago:20250902T183000",
        "DTEND;TZID=America/Chicago:20250902T200000",
        f"RRULE:{rule}",
        *(f"EXDATE;TZID=America/Chicago:{exdate}" for exdate in exdates),
        "SUMMARY:Pottery class",
        "LOCATION:Kilnworks Studio",
        "ORGANIZER;CN=Argus McNevans:mailto:argus@example.com",
        *alarm,
        "END:VEVENT",
    ]


def moved_occurrence(
    occurrence: str = "20261027T183000",
    start: str = "20261029T183000",
    *,
    sequence: int = 1,
    dtstamp: str = "20261020T131500Z",
) -> list[str]:
    """An override: one occurrence of the class, moved."""
    return [
        "BEGIN:VEVENT",
        f"UID:{POTTERY_UID}",
        f"RECURRENCE-ID;TZID=America/Chicago:{occurrence}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{dtstamp}",
        "LAST-MODIFIED:20261019T090000Z",
        f"DTSTART;TZID=America/Chicago:{start}",
        f"DTEND;TZID=America/Chicago:{start[:9]}200000",
        "SUMMARY:Pottery class",
        "LOCATION:Kilnworks Studio",
        "END:VEVENT",
    ]


def resource(*events: list[str], with_timezone: bool = True) -> list[str]:
    """A calendar resource as a CalDAV server serves it: one VCALENDAR."""
    return [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Apple Inc.//iCloud 2.0//EN",
        "CALSCALE:GREGORIAN",
        *(TIMEZONE if with_timezone else []),
        *(line for event in events for line in event),
        "END:VCALENDAR",
    ]


def captured(
    lines: list[str],
    *,
    calendar: str | None = CALENDAR,
    captured_at: str | None = CAPTURED_AT,
    deleted_at: str | None = None,
) -> bytes:
    """The resource behind the collector's capture header."""
    fields = [("Calendar", calendar), ("Captured-At", captured_at), ("Deleted-At", deleted_at)]
    header = "".join(f"{name}: {value}\n" for name, value in fields if value is not None)
    return f"{header}\n".encode() + "".join(f"{line}\r\n" for line in lines).encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    return root


def ingest(data_root: Path, payloads: dict[str, bytes]) -> None:
    for name, payload in payloads.items():
        target = data_root / "inbox" / "icloud_calendar" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    assert main(["ingest"]) == 0


def episodes(data_root: Path) -> list[dict[str, Any]]:
    directory = data_root / "l0" / "episodes" / "icloud_calendar"
    return [json.loads(path.read_bytes()) for path in sorted(directory.rglob("*.json"))]


def quarantine_reasons(data_root: Path) -> list[str]:
    directory = data_root / "l0" / "quarantine"
    if not directory.exists():
        return []
    records = [json.loads(path.read_bytes()) for path in sorted(directory.glob("*.json"))]
    return [record["reason"] for record in records if "reason" in record]


def test_a_headered_event_lands_on_its_headers_calendar_captured_when_the_header_says(
    data_root: Path,
):
    payload = captured(resource(dentist()))

    ingest(data_root, {"dentist.ics": payload})

    [episode] = episodes(data_root)
    assert episode["calendar_name"] == CALENDAR
    assert episode["captured_at"] == "2026-10-20T13:15:00Z"
    assert episode["subject"] == "Dentist, Wren"
    assert episode["native_id"] == episode["thread_ref"] == "dentist-0001@example.com"
    assert episode["occurred_at"] == {
        "start": "2026-10-28T15:00:00-05:00",
        "end": "2026-10-28T16:00:00-05:00",
    }
    assert (data_root / "l0" / episode["raw_ref"]).read_bytes() == payload
    assert quarantine_reasons(data_root) == []


ENDS = {
    "a time with a duration": (
        ["DTSTART;TZID=America/Chicago:20261028T150000", "DURATION:PT1H30M"],
        {"start": "2026-10-28T15:00:00-05:00", "end": "2026-10-28T16:30:00-05:00"},
    ),
    "a day with a duration": (
        ["DTSTART;VALUE=DATE:20261028", "DURATION:P2D"],
        {"start": "2026-10-28T00:00:00Z", "end": "2026-10-30T00:00:00Z"},
    ),
    "a time alone": (
        ["DTSTART;TZID=America/Chicago:20261028T150000"],
        {"start": "2026-10-28T15:00:00-05:00", "end": "2026-10-28T15:00:00-05:00"},
    ),
    "a day alone": (
        ["DTSTART;VALUE=DATE:20261028"],
        {"start": "2026-10-28T00:00:00Z", "end": "2026-10-29T00:00:00Z"},
    ),
}


@pytest.mark.parametrize("given", ENDS, ids=list(ENDS))
def test_an_event_without_a_dtend_ends_as_icalendar_says(data_root: Path, given: str):
    times, occurred_at = ENDS[given]
    event = [
        "BEGIN:VEVENT",
        "UID:open-house-0001@example.com",
        "DTSTAMP:20261020T131500Z",
        *times,
        "SUMMARY:Open house",
        "END:VEVENT",
    ]

    ingest(data_root, {"open-house.ics": captured(resource(event))})

    [episode] = episodes(data_root)
    assert episode["occurred_at"] == occurred_at


HEADER_FAULTS = {
    "no calendar": ({"calendar": None}, "has no Calendar in its capture header"),
    "empty calendar": ({"calendar": ""}, "has no Calendar in its capture header"),
    "no capture time": ({"captured_at": None}, "has no Captured-At in its capture header"),
    "capture time without an offset": (
        {"captured_at": "2026-10-20T08:15:00"},
        "Captured-At is not a time with a UTC offset",
    ),
    "deletion time that isn't one": (
        {"deleted_at": "Tuesday at Bramble Dental"},
        "Deleted-At is not a time with a UTC offset",
    ),
}


@pytest.mark.parametrize("fault", HEADER_FAULTS, ids=list(HEADER_FAULTS))
def test_a_header_missing_a_required_field_is_quarantined_without_quoting_it(
    data_root: Path, fault: str
):
    fields, reason = HEADER_FAULTS[fault]

    ingest(data_root, {"dentist.ics": captured(resource(dentist()), **fields)})

    assert episodes(data_root) == []
    [quarantined] = quarantine_reasons(data_root)
    assert reason in quarantined
    assert "Bramble" not in quarantined
    assert "2026-10-20T08:15" not in quarantined


def test_a_deletion_is_its_own_episode_saying_the_event_was_deleted(data_root: Path):
    last_known = resource(dentist())
    ingest(data_root, {"dentist.ics": captured(last_known)})
    deleted = captured(last_known, deleted_at="2026-10-22T19:40:00-05:00")

    ingest(data_root, {"dentist.ics": deleted})

    version, deletion = sorted(episodes(data_root), key=lambda e: e["captured_at"])
    assert deletion["episode_id"] != version["episode_id"]
    assert deletion["thread_ref"] == version["thread_ref"]
    assert deletion["captured_at"] == "2026-10-23T00:40:00Z"
    assert deletion["body"].startswith("Status: deleted\n")
    assert version["body"].startswith("Status: confirmed\n")
    assert (data_root / "l0" / deletion["raw_ref"]).read_bytes() == deleted
    assert quarantine_reasons(data_root) == []


def test_a_series_with_its_overrides_is_one_episode_of_the_whole_series(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    series = resource(
        pottery(), moved_occurrence(), moved_occurrence("20261103T183000", "20261104T183000")
    )

    ingest(data_root, {"pottery.ics": captured(series)})

    [episode] = episodes(data_root)
    assert episode["native_id"] == episode["thread_ref"] == POTTERY_UID
    assert episode["subject"] == "Pottery class"
    assert episode["calendar_name"] == CALENDAR
    # A series starts with its first occurrence and has no one end: its occurrences are listed
    # in its body.
    assert episode["occurred_at"] == {"start": "2025-09-02T18:30:00-05:00", "end": None}
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_changing_one_override_is_a_new_version_of_the_series(data_root: Path):
    ingest(data_root, {"pottery.ics": captured(resource(pottery(), moved_occurrence()))})
    # Moved again, a day later: only the override changes.
    changed = resource(pottery(), moved_occurrence(start="20261030T183000", sequence=2))

    ingest(data_root, {"pottery.ics": captured(changed, captured_at="2026-10-21T08:15:00-05:00")})

    first, second = sorted(episodes(data_root), key=lambda e: e["captured_at"])
    assert first["episode_id"] != second["episode_id"]
    assert first["thread_ref"] == second["thread_ref"] == POTTERY_UID
    assert quarantine_reasons(data_root) == []


def test_a_change_without_a_sequence_or_last_modified_bump_is_a_new_version(data_root: Path):
    # The spike saw iCloud end a series ("all future events") with an UNTIL and bump nothing.
    ingest(data_root, {"pottery.ics": captured(resource(pottery()))})
    ended = resource(pottery(rule="FREQ=WEEKLY;BYDAY=TU;UNTIL=20261020T045959Z"))

    ingest(data_root, {"pottery.ics": captured(ended, captured_at="2026-10-21T08:15:00-05:00")})

    assert len({e["episode_id"] for e in episodes(data_root)}) == 2
    assert quarantine_reasons(data_root) == []


def test_an_event_changed_without_a_bump_is_a_new_version(data_root: Path):
    ingest(data_root, {"dentist.ics": captured(resource(dentist()))})
    reworded = resource(dentist(description="Bring the referral form and the insurance card."))

    ingest(data_root, {"dentist.ics": captured(reworded, captured_at="2026-10-21T08:15:00-05:00")})

    assert len({e["episode_id"] for e in episodes(data_root)}) == 2
    assert quarantine_reasons(data_root) == []


def test_an_unchanged_series_captured_again_later_collapses(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    ingest(data_root, {"pottery.ics": captured(resource(pottery(), moved_occurrence()))})
    capsys.readouterr()
    # Served again with every DTSTAMP new, the overrides in another order, and read a day later.
    later = "20261021T131500Z"
    again = resource(moved_occurrence(dtstamp=later), pottery(dtstamp=later))

    ingest(data_root, {"pottery.ics": captured(again, captured_at="2026-10-21T08:15:00-05:00")})

    [episode] = episodes(data_root)
    assert episode["captured_at"] == "2026-10-20T13:15:00Z"
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"


def test_an_event_moved_to_another_calendar_is_a_new_version_there(data_root: Path):
    # The spike saw a move between calendars as a deletion from one and a new resource with
    # the same UID in the other.
    ingest(data_root, {"dentist.ics": captured(resource(dentist()))})

    ingest(data_root, {"dentist.ics": captured(resource(dentist()), calendar="Moonwhistle Home")})

    assert sorted(e["calendar_name"] for e in episodes(data_root)) == [
        CALENDAR,
        "Moonwhistle Home",
    ]
    assert quarantine_reasons(data_root) == []


def test_a_change_to_an_alarm_alone_collapses_as_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    ingest(data_root, {"pottery.ics": captured(resource(pottery()))})
    capsys.readouterr()
    # Dismissing a reminder marks its alarm acknowledged; moving it changes its trigger.
    acknowledged = [*ALARM[:-1], "ACKNOWLEDGED:20261020T230000Z", "END:VALARM"]
    earlier = [line.replace("-PT30M", "-PT1H") for line in ALARM]
    later = "2026-10-21T08:15:00-05:00"

    ingest(
        data_root,
        {
            "pottery.ics": captured(resource(pottery(alarm=acknowledged)), captured_at=later),
            "pottery-again.ics": captured(resource(pottery(alarm=earlier)), captured_at=later),
            "pottery-no-alarm.ics": captured(resource(pottery(alarm=[])), captured_at=later),
        },
    )

    assert len(episodes(data_root)) == 1
    assert capsys.readouterr().out == "ingested 0, unchanged 3, quarantined 0\n"


def series_from_the_move(start: str = "20261020T190000") -> list[str]:
    """The new series a move of "all future events" starts, in a resource of its own."""
    return [
        "BEGIN:VEVENT",
        "UID:pottery-0002@example.com",
        "SEQUENCE:0",
        "DTSTAMP:20261020T131500Z",
        f"DTSTART;TZID=America/Chicago:{start}",
        f"DTEND;TZID=America/Chicago:{start[:9]}203000",
        "RRULE:FREQ=WEEKLY;BYDAY=TU",
        "SUMMARY:Pottery class",
        "END:VEVENT",
    ]


def test_a_split_series_is_two_episodes_one_ended_and_one_new(data_root: Path):
    ingest(data_root, {"pottery.ics": captured(resource(pottery()))})
    ended = resource(pottery(rule="FREQ=WEEKLY;BYDAY=TU;UNTIL=20261020T045959Z"))
    later = "2026-10-21T08:15:00-05:00"

    ingest(
        data_root,
        {
            "pottery.ics": captured(ended, captured_at=later),
            "pottery-moved.ics": captured(resource(series_from_the_move()), captured_at=later),
        },
    )

    threads = sorted(e["thread_ref"] for e in episodes(data_root))
    assert threads == [POTTERY_UID, POTTERY_UID, "pottery-0002@example.com"]
    assert quarantine_reasons(data_root) == []


SHAPE_FAULTS = {
    "two series in one resource": (
        resource(pottery(), series_from_the_move()),
        "calendar resource has events with 2 UIDs; expected one series",
    ),
    "overrides without their series": (
        resource(moved_occurrence(), moved_occurrence("20261103T183000", "20261104T183000")),
        "calendar resource has 0 events without a RECURRENCE-ID; expected one series master",
    ),
    "two masters of one series": (
        resource(pottery(), pottery()),
        "calendar resource has 2 events without a RECURRENCE-ID; expected one series master",
    ),
    "no events": (resource(), "calendar resource has no events"),
}


@pytest.mark.parametrize("fault", SHAPE_FAULTS, ids=list(SHAPE_FAULTS))
def test_a_resource_that_is_not_one_event_or_one_series_is_quarantined(data_root: Path, fault: str):
    lines, reason = SHAPE_FAULTS[fault]

    ingest(data_root, {"pottery.ics": captured(lines)})

    assert episodes(data_root) == []
    assert quarantine_reasons(data_root) == [reason]


def test_a_deleted_series_is_its_own_episode(data_root: Path):
    series = resource(pottery(), moved_occurrence())
    ingest(data_root, {"pottery.ics": captured(series)})

    ingest(data_root, {"pottery.ics": captured(series, deleted_at="2026-10-22T19:40:00-05:00")})

    version, deletion = sorted(episodes(data_root), key=lambda e: e["captured_at"])
    assert deletion["episode_id"] != version["episode_id"]
    assert deletion["body"].startswith("Status: deleted")
    assert deletion["occurred_at"] == version["occurred_at"]


def test_renormalize_rebuilds_every_captured_resource_as_it_was(data_root: Path):
    series = resource(pottery(), moved_occurrence())
    ingest(
        data_root,
        {
            "dentist.ics": captured(resource(dentist())),
            "pottery.ics": captured(series),
            "pottery-deleted.ics": captured(series, deleted_at="2026-10-22T19:40:00-05:00"),
        },
    )
    before = episodes(data_root)

    assert main(["renormalize"]) == 0

    assert episodes(data_root) == before
    assert len(before) == 3


def test_a_deletion_landed_again_by_reconciliation_collapses(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    last_known = resource(dentist())
    ingest(data_root, {"dentist.ics": captured(last_known, deleted_at="2026-10-22T19:40:00-05:00")})
    capsys.readouterr()

    ingest(data_root, {"dentist.ics": captured(last_known, deleted_at="2026-10-23T02:00:00-05:00")})

    assert len(episodes(data_root)) == 1
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"
