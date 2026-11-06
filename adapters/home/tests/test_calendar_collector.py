"""The iCloud Calendar collector, through the capture cycle with a fake calendar server.

Tests assert on what a cycle leaves behind (L0, the inbox, the status file) and on what the
server was asked for, never on the collector's internals. The fake scripts an account's
calendars: resources with etags, sync tokens that move with every change, removed resources,
changes a sync report leaves out, and an expired token. Every fixture is synthetic, shaped like
what the calendar structure spike (#85) found on iCloud.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.envelope import Envelope, Source
from pa_core.owner import Owner
from pa_core.status import read_status
from pa_home import cli
from pa_home.capture import CycleOutcome, capture_cycle
from pa_home.cli import main
from pa_home.collectors.calendar import CalendarCollector
from pa_home.collectors.calendar_server import (
    CalendarServerError,
    Changes,
    Collection,
    Listing,
    Members,
    Resource,
    SyncTokenRejected,
)
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0

CHICAGO = ZoneInfo("America/Chicago")
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
CADENCES = {Source.ICLOUD_CALENDAR: timedelta(minutes=15)}
START = datetime(2026, 10, 20, 8, 15, tzinfo=CHICAGO)
WINDOW_START = START - timedelta(days=60)
HOME = "Moonwhistle Home"
FAMILY = "Moonwhistle Family"


def event(
    uid: str,
    start: str,
    *,
    summary: str = "Dentist",
    rule: str | None = None,
    sequence: int = 0,
    dtstamp: str = "20261020T131500Z",
) -> list[str]:
    """A VEVENT: a one-off of an hour, or a series' master with `rule`."""
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;TZID=America/Chicago:{start}",
        f"DTEND;TZID=America/Chicago:{start[:9]}{int(start[9:11]) + 1:02d}{start[11:]}",
        *([f"RRULE:{rule}"] if rule else []),
        f"SUMMARY:{summary}",
        "LOCATION:Bramble Dental",
        "END:VEVENT",
    ]


def served(*events: list[str]) -> bytes:
    """A calendar resource as iCloud serves it, its line breaks as XML gives them back."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Apple Inc.//iCloud 2.0//EN",
        *(line for one in events for line in one),
        "END:VCALENDAR",
    ]
    return "".join(f"{line}\n" for line in lines).encode()


@dataclass
class FakeCalendar:
    collection: Collection
    resources: dict[str, Resource] = field(default_factory=dict)
    history: list[tuple[str, int]] = field(default_factory=list)
    """Each change's href, with the token it was made at."""


class FakeServer:
    """A CalDAV account: its calendars, each with a sync token that moves with every change."""

    def __init__(self) -> None:
        self.calendars: dict[str, FakeCalendar] = {}
        self.token = 0
        self.earliest_token = 0
        """Tokens before this one are refused, as an expired token is."""
        self.fetched: list[str] = []
        self.down: CalendarServerError | None = None
        self.broken: dict[str, CalendarServerError] = {}
        """Calendars whose requests fail, by name, while the rest answer."""

    def add_calendar(self, name: str) -> None:
        url = f"https://p01-caldav.example.com/111/calendars/c{len(self.calendars)}/"
        self.calendars[name] = FakeCalendar(Collection(url, name, ("VEVENT",)))

    def put(self, calendar: str, item: str, data: bytes, *, unreported: bool = False) -> str:
        """Adds or replaces a resource, under a new etag. A server may leave a change out of
        its sync reports. Returns the resource's href."""
        self.token += 1
        found = self.calendars[calendar]
        href = f"{found.collection.url}{item}.ics"
        found.resources[href] = Resource(href, f'"etag-{self.token}"', data)
        if not unreported:
            found.history.append((href, self.token))
        return href

    def touch(self, calendar: str, item: str) -> None:
        """Reports a resource in the next sync report without changing it, as a server may
        when something about it other than its bytes changed."""
        self.token += 1
        found = self.calendars[calendar]
        found.history.append((f"{found.collection.url}{item}.ics", self.token))

    def delete(self, calendar: str, item: str, *, unreported: bool = False) -> None:
        self.token += 1
        found = self.calendars[calendar]
        href = f"{found.collection.url}{item}.ics"
        del found.resources[href]
        if not unreported:
            found.history.append((href, self.token))

    # The CalendarServer interface.

    def collections(self) -> list[Collection]:
        self._check()
        return [calendar.collection for calendar in self.calendars.values()]

    def listing(self, collection: Collection) -> Listing:
        members = self.members(collection)
        return Listing(members.sync_token, self.fetch(collection, list(members.etags)))

    def members(self, collection: Collection) -> Members:
        self._check()
        calendar = self._calendar(collection)
        etags = {href: resource.etag for href, resource in calendar.resources.items()}
        return Members(self._token(), etags)

    def fetch(self, collection: Collection, hrefs: Sequence[str]) -> list[Resource]:
        self._check()
        calendar = self._calendar(collection)
        self.fetched += hrefs
        return [calendar.resources[href] for href in hrefs if href in calendar.resources]

    def changes(self, collection: Collection, sync_token: str) -> Changes:
        self._check()
        since = int(sync_token.rpartition("-")[2])
        if since < self.earliest_token:
            raise SyncTokenRejected(403)
        calendar = self._calendar(collection)
        touched = {href for href, token in calendar.history if token > since}
        changed = {
            href: calendar.resources[href].etag for href in touched if href in calendar.resources
        }
        removed = [href for href in touched if href not in calendar.resources]
        return Changes(self._token(), changed, removed)

    def _calendar(self, collection: Collection) -> FakeCalendar:
        found = next(c for c in self.calendars.values() if c.collection.url == collection.url)
        if found.collection.name in self.broken:
            raise self.broken[found.collection.name]
        return found

    def _token(self) -> str:
        return f"token-{self.token}"

    def _check(self) -> None:
        if self.down is not None:
            raise self.down


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


@pytest.fixture
def server() -> FakeServer:
    server = FakeServer()
    server.add_calendar(HOME)
    server.add_calendar(FAMILY)
    server.add_calendar("Moonwhistle Work")
    return server


def run_cycle(
    data_root: DataRoot,
    server: FakeServer,
    clock: Clock,
    *,
    calendars: tuple[str, ...] = (HOME, FAMILY),
    window_start: datetime | None = None,
    reconcile: bool = False,
) -> CycleOutcome:
    return capture_cycle(
        data_root,
        collectors=[CalendarCollector(lambda: server, calendars, CHICAGO)],
        owner=OWNER,
        cadences=CADENCES,
        now=clock,
        window_start=window_start,
        reconcile=reconcile,
    )


def backfill(data_root: DataRoot, server: FakeServer, clock: Clock) -> CycleOutcome:
    return run_cycle(data_root, server, clock, window_start=WINDOW_START)


def versions(data_root: DataRoot) -> list[Envelope]:
    """Every calendar episode in L0, oldest first."""
    found = [
        envelope
        for envelope in FilesystemL0(data_root.l0).envelopes()
        if envelope.source == Source.ICLOUD_CALENDAR
    ]
    return sorted(found, key=lambda envelope: (envelope.captured_at, envelope.episode_id))


def raw_payload(data_root: DataRoot, envelope: Envelope) -> bytes:
    return (data_root.l0 / envelope.raw_ref).read_bytes()


def files_under(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def test_a_backfill_lands_each_resource_as_served_behind_its_capture_header(
    data_root: DataRoot, server: FakeServer
):
    dentist = served(event("dentist-1@example.com", "20261028T150000"))
    pottery = served(event("pottery-1@example.com", "20261021T183000", summary="Pottery"))
    server.put(HOME, "dentist", dentist)
    server.put(FAMILY, "pottery", pottery)
    server.put(
        "Moonwhistle Work", "standup", served(event("standup@example.com", "20261021T090000"))
    )
    clock = Clock(START)

    outcome = backfill(data_root, server, clock)

    assert outcome.failures == {}
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 2}
    landed = {envelope.native_id: envelope for envelope in versions(data_root)}
    assert sorted(landed) == ["dentist-1@example.com", "pottery-1@example.com"]
    assert landed["dentist-1@example.com"].calendar_name == HOME
    assert landed["pottery-1@example.com"].calendar_name == FAMILY
    assert raw_payload(data_root, landed["dentist-1@example.com"]) == (
        f"Calendar: {HOME}\nCaptured-At: 2026-10-20T08:15:00-05:00\n\n".encode() + dentist
    )
    assert landed["dentist-1@example.com"].captured_at == START
    assert files_under(data_root.inbox) == []
    status = read_status(data_root.status)
    assert status.sources[Source.ICLOUD_CALENDAR].watermark == START


def test_a_backfill_takes_what_occurs_in_its_window_and_everything_upcoming(
    data_root: DataRoot, server: FakeServer
):
    # The window starts on 2026-08-21.
    server.put(HOME, "old-dentist", served(event("old-dentist@example.com", "20260301T150000")))
    weekly = "FREQ=WEEKLY;BYDAY=TU"
    server.put(
        HOME, "pottery", served(event("pottery@example.com", "20250902T183000", rule=weekly))
    )
    ended = "FREQ=WEEKLY;BYDAY=WE;UNTIL=20260601T000000Z"
    server.put(HOME, "choir", served(event("choir@example.com", "20250903T190000", rule=ended)))
    server.put(FAMILY, "recital", served(event("recital@example.com", "20261205T140000")))
    server.put(FAMILY, "picnic", served(event("picnic@example.com", "20260905T120000")))
    clock = Clock(START)

    backfill(data_root, server, clock)

    assert sorted(envelope.native_id for envelope in versions(data_root)) == [
        "picnic@example.com",
        "pottery@example.com",
        "recital@example.com",
    ]
    # What the backfill left out stays out, through collections and reconciliation.
    clock.now += timedelta(minutes=15)
    assert run_cycle(data_root, server, clock).collected == {Source.ICLOUD_CALENDAR: 0}
    clock.now += timedelta(hours=8)
    server.fetched.clear()
    outcome = run_cycle(data_root, server, clock, reconcile=True)
    assert outcome.failures == {}
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 0}
    assert server.fetched == []
    assert len(versions(data_root)) == 3


def test_a_resource_the_normalizer_cant_read_is_taken_and_quarantined_in_sight(
    data_root: DataRoot, server: FakeServer
):
    two_uids = served(
        event("one@example.com", "20250101T090000"), event("two@example.com", "20250101T100000")
    )
    server.put(HOME, "odd", two_uids)

    outcome = backfill(data_root, server, Clock(START))

    assert outcome.collected == {Source.ICLOUD_CALENDAR: 1}
    assert outcome.ingested is not None and outcome.ingested.quarantined == 1
    status = read_status(data_root.status).sources[Source.ICLOUD_CALENDAR]
    assert status.quarantined == 1
    assert status.watermark == START


def test_a_collection_lands_what_the_sync_report_says_is_new_or_changed(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    server.put(HOME, "pottery", served(event("pottery@example.com", "20261021T183000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    moved = served(event("dentist@example.com", "20261029T150000", sequence=1))
    dentist = server.put(HOME, "dentist", moved)
    recital = server.put(FAMILY, "recital", served(event("recital@example.com", "20261205T140000")))
    clock.now = START + timedelta(minutes=15)
    server.fetched.clear()
    outcome = run_cycle(data_root, server, clock)

    assert outcome.failures == {}
    assert sorted(server.fetched) == sorted([dentist, recital])
    assert outcome.ingested is not None and outcome.ingested.ingested == 2
    dentists = [e for e in versions(data_root) if e.native_id == "dentist@example.com"]
    assert [e.occurred_at.start.day for e in dentists] == [28, 29]
    assert dentists[-1].captured_at == clock.now
    assert raw_payload(data_root, dentists[-1]).endswith(moved)
    assert read_status(data_root.status).sources[Source.ICLOUD_CALENDAR].watermark == clock.now


def test_an_unchanged_resource_is_never_fetched_again_and_a_new_etag_alone_collapses(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    pottery = server.put(HOME, "pottery", served(event("pottery@example.com", "20261021T183000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    # The server serves the class again under a new etag, its DTSTAMP the only difference.
    restamped = served(event("pottery@example.com", "20261021T183000", dtstamp="20261020T150000Z"))
    server.put(HOME, "pottery", restamped)
    server.touch(HOME, "dentist")
    clock.now = START + timedelta(minutes=15)
    server.fetched.clear()
    outcome = run_cycle(data_root, server, clock)

    assert server.fetched == [pottery]
    assert outcome.ingested is not None
    assert (outcome.ingested.ingested, outcome.ingested.unchanged) == (0, 1)
    assert len(versions(data_root)) == 2
    assert files_under(data_root.inbox) == []

    clock.now += timedelta(minutes=15)
    server.fetched.clear()
    outcome = run_cycle(data_root, server, clock)
    assert server.fetched == []
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 0}


def test_a_removal_in_a_sync_report_lands_as_a_deletion_of_the_last_known_bytes(
    data_root: DataRoot, server: FakeServer
):
    dentist = served(event("dentist@example.com", "20261028T150000"))
    server.put(HOME, "dentist", dentist)
    clock = Clock(START)
    backfill(data_root, server, clock)

    server.delete(HOME, "dentist")
    clock.now = START + timedelta(hours=2)
    outcome = run_cycle(data_root, server, clock)

    assert outcome.failures == {}
    first, deletion = versions(data_root)
    assert deletion.episode_id != first.episode_id
    assert deletion.captured_at == clock.now
    assert deletion.body.startswith("Status: deleted")
    assert raw_payload(data_root, deletion) == (
        f"Calendar: {HOME}\nCaptured-At: 2026-10-20T08:15:00-05:00\n"
        "Deleted-At: 2026-10-20T10:15:00-05:00\n\n".encode()
        + dentist
    )
    # Landed once: the deletion isn't landed again by later collections or reconciliation.
    clock.now += timedelta(minutes=15)
    assert run_cycle(data_root, server, clock).collected == {Source.ICLOUD_CALENDAR: 0}
    clock.now += timedelta(hours=8)
    assert run_cycle(data_root, server, clock, reconcile=True).collected == {
        Source.ICLOUD_CALENDAR: 0
    }
    assert len(versions(data_root)) == 2


def test_reconciliation_lands_what_the_sync_reports_missed(data_root: DataRoot, server: FakeServer):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    server.put(FAMILY, "pottery", served(event("pottery@example.com", "20261021T183000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    server.delete(HOME, "dentist", unreported=True)
    moved = served(event("pottery@example.com", "20261022T183000", sequence=1))
    server.put(FAMILY, "pottery", moved, unreported=True)
    clock.now = START + timedelta(minutes=15)
    assert run_cycle(data_root, server, clock).collected == {Source.ICLOUD_CALENDAR: 0}

    clock.now = START + timedelta(hours=18)
    outcome = run_cycle(data_root, server, clock, reconcile=True)

    assert outcome.failures == {}
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 2}
    latest = {e.native_id: e for e in versions(data_root)}
    assert latest["dentist@example.com"].body.startswith("Status: deleted")
    assert latest["dentist@example.com"].captured_at == clock.now
    assert latest["pottery@example.com"].occurred_at.start.day == 22
    assert read_status(data_root.status).sources[Source.ICLOUD_CALENDAR].watermark == clock.now


def test_an_expired_token_leads_to_a_full_listing_without_duplicates(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    server.put(HOME, "pottery", served(event("pottery@example.com", "20261021T183000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    # The devbox was off for a fortnight, and the server no longer takes the token.
    server.delete(HOME, "dentist")
    recital = server.put(HOME, "recital", served(event("recital@example.com", "20261205T140000")))
    server.earliest_token = server.token + 1
    clock.now = START + timedelta(days=14)
    server.fetched.clear()
    outcome = run_cycle(data_root, server, clock)

    assert outcome.failures == {}
    assert server.fetched == [recital]
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 2}
    assert outcome.ingested is not None
    assert (outcome.ingested.ingested, outcome.ingested.unchanged) == (2, 0)
    latest = {e.native_id: e for e in versions(data_root)}
    assert sorted(latest) == ["dentist@example.com", "pottery@example.com", "recital@example.com"]
    assert latest["dentist@example.com"].body.startswith("Status: deleted")

    # The listing's token is good from here on.
    clock.now += timedelta(minutes=15)
    server.put(HOME, "pottery", served(event("pottery@example.com", "20261022T183000")))
    assert run_cycle(data_root, server, clock).collected == {Source.ICLOUD_CALENDAR: 1}


def test_a_client_failure_leaves_the_watermark_stale_and_records_the_failure(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    server.put(HOME, "dentist", served(event("dentist@example.com", "20261029T150000")))
    server.down = CalendarServerError("listing the calendars", 503)
    clock.now = START + timedelta(minutes=15)
    outcome = run_cycle(data_root, server, clock)

    assert outcome.failures == {Source.ICLOUD_CALENDAR: "listing the calendars failed (HTTP 503)"}
    status = read_status(data_root.status).sources[Source.ICLOUD_CALENDAR]
    assert status.watermark == START
    assert status.last_failure is not None
    assert status.last_failure.at == clock.now
    assert status.last_attempt == clock.now

    # Once the server answers again, the change lands and the watermark moves.
    server.down = None
    clock.now += timedelta(minutes=15)
    outcome = run_cycle(data_root, server, clock)
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 1}
    assert read_status(data_root.status).sources[Source.ICLOUD_CALENDAR].watermark == clock.now


def test_one_failing_calendar_fails_the_collection_but_the_others_still_land(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))
    server.put(FAMILY, "pottery", served(event("pottery@example.com", "20261021T183000")))
    clock = Clock(START)
    backfill(data_root, server, clock)

    server.put(HOME, "dentist", served(event("dentist@example.com", "20261029T150000")))
    server.put(FAMILY, "pottery", served(event("pottery@example.com", "20261022T183000")))
    server.broken[FAMILY] = CalendarServerError("the sync report", 500)
    clock.now = START + timedelta(minutes=15)
    outcome = run_cycle(data_root, server, clock)

    assert outcome.failures == {
        Source.ICLOUD_CALENDAR: f"{FAMILY}: the sync report failed (HTTP 500)"
    }
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 1}
    assert read_status(data_root.status).sources[Source.ICLOUD_CALENDAR].watermark == START

    # The failed calendar's change waits for the next collection, from its old token.
    del server.broken[FAMILY]
    clock.now += timedelta(minutes=15)
    outcome = run_cycle(data_root, server, clock)
    assert outcome.failures == {}
    assert outcome.collected == {Source.ICLOUD_CALENDAR: 1}
    latest = {e.native_id: e for e in versions(data_root)}
    assert latest["pottery@example.com"].occurred_at.start.day == 22


def test_a_newline_in_a_calendar_name_cant_add_a_deleted_at(
    data_root: DataRoot, server: FakeServer
):
    forged = f"{HOME}\nDeleted-At: 2026-10-01T00:00:00-05:00"
    server.add_calendar(forged)
    dentist = served(event("dentist@example.com", "20261028T150000"))
    server.put(forged, "dentist", dentist)

    outcome = run_cycle(
        data_root, server, Clock(START), calendars=(forged,), window_start=WINDOW_START
    )

    assert outcome.failures == {}
    [landed] = versions(data_root)
    assert not landed.body.startswith("Status: deleted")
    assert raw_payload(data_root, landed) == (
        f"Calendar: {HOME} Deleted-At: 2026-10-01T00:00:00-05:00\n"
        "Captured-At: 2026-10-20T08:15:00-05:00\n\n".encode()
        + dentist
    )


def test_a_calendar_name_with_a_control_character_fails_that_calendar_alone(
    data_root: DataRoot, server: FakeServer
):
    odd = f"{HOME}\x1b[2J"
    server.add_calendar(odd)
    server.put(odd, "dentist", served(event("dentist@example.com", "20261028T150000")))
    server.put(FAMILY, "pottery", served(event("pottery@example.com", "20261021T183000")))

    outcome = run_cycle(
        data_root, server, Clock(START), calendars=(odd, FAMILY), window_start=WINDOW_START
    )

    assert outcome.failures == {
        Source.ICLOUD_CALENDAR: "a calendar's display name holds a control character, so it "
        "can't go in a capture header"
    }
    assert [e.native_id for e in versions(data_root)] == ["pottery@example.com"]


def test_a_wrong_password_says_to_save_a_new_app_specific_password(
    data_root: DataRoot, server: FakeServer
):
    server.down = CalendarServerError("finding the calendar home", 401)

    outcome = backfill(data_root, server, Clock(START))

    reason = outcome.failures[Source.ICLOUD_CALENDAR]
    assert reason.startswith("finding the calendar home failed (HTTP 401): ")
    assert "app-specific password" in reason


def test_a_collection_before_any_backfill_fails_and_says_what_to_do(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))

    outcome = run_cycle(data_root, server, Clock(START), calendars=(HOME,))

    assert outcome.failures == {
        Source.ICLOUD_CALENDAR: f"{HOME}: no sync token yet: run a backfill first"
    }
    assert versions(data_root) == []
    assert read_status(data_root.status).sources[Source.ICLOUD_CALENDAR].watermark is None


def test_a_calendar_the_account_doesnt_serve_is_named_in_the_failure(
    data_root: DataRoot, server: FakeServer
):
    server.put(HOME, "dentist", served(event("dentist@example.com", "20261028T150000")))

    outcome = run_cycle(
        data_root, server, Clock(START), calendars=(HOME, "Birthdays"), window_start=WINDOW_START
    )

    assert outcome.failures == {
        Source.ICLOUD_CALENDAR: "Birthdays: the account serves no calendar by that name"
    }
    assert [e.native_id for e in versions(data_root)] == ["dentist@example.com"]


# `pa capture` and `pa calendar`, configured as the owner would configure them.

# Distinctive text that must never reach the output, the logs, the status file, or a file name.
SALT = "Quillon Vasquez-Ferro at the Bellwether loft"
SALTED_UID = "quillon-vasquez-ferro@bellwether.example"


def salted(item: str, start: str, *, rule: str | None = None) -> bytes:
    return served(event(f"{item}-{SALTED_UID}", start, summary=SALT, rule=rule))


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataRoot:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_ICLOUD_CALENDARS", f"{HOME},{FAMILY}")
    return DataRoot(root)


@pytest.fixture
def fake_account(server: FakeServer, monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    """The fake server stands in for the owner's iCloud account wherever `pa` opens it."""
    monkeypatch.setattr(cli, "open_icloud", lambda: server)
    return server


def days_ago(days: float) -> datetime:
    return datetime.now(CHICAGO) - timedelta(days=days)


def ics_time(moment: datetime) -> str:
    return moment.astimezone(CHICAGO).strftime("%Y%m%dT100000")


def test_the_dry_run_prints_counts_per_calendar_only(
    configured: DataRoot,
    fake_account: FakeServer,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    fake_account.put(HOME, "soon", salted("soon", ics_time(days_ago(-3))))
    fake_account.put(HOME, "recent", salted("recent", ics_time(days_ago(10))))
    fake_account.put(HOME, "old", salted("old", ics_time(days_ago(90))))
    fake_account.put(
        FAMILY, "weekly", salted("weekly", ics_time(days_ago(400)), rule="FREQ=WEEKLY")
    )
    fake_account.put(
        FAMILY, "odd", served(event("a@x", "20250101T090000"), event("b@x", "20250101T100000"))
    )
    fake_account.put("Moonwhistle Work", "standup", salted("standup", ics_time(days_ago(1))))
    window_start = days_ago(60).date().isoformat()

    assert main(["calendar", "dry-run", "--window-start", window_start]) == 0

    output = capsys.readouterr()
    assert output.out == (
        f"calendar: {HOME}: 3 resources: 2 with an occurrence since {window_start}, "
        "1 before, 0 unreadable\n"
        f"calendar: {FAMILY}: 2 resources: 1 with an occurrence since {window_start}, "
        "0 before, 1 unreadable\n"
        f"calendar: a backfill from {window_start} takes 4 of 5 resources\n"
    )
    for text in (SALT, SALTED_UID, "p01-caldav"):
        assert text not in output.out + output.err + caplog.text
    assert not configured.path.exists()


def test_the_calendars_command_names_the_calendars_the_account_serves(
    configured: DataRoot, fake_account: FakeServer, capsys: pytest.CaptureFixture[str]
):
    assert main(["calendar", "calendars"]) == 0

    assert capsys.readouterr().out == (
        f"calendar: {HOME} (VEVENT), captured\n"
        f"calendar: {FAMILY} (VEVENT), captured\n"
        "calendar: Moonwhistle Work (VEVENT)\n"
    )


def test_pa_capture_backfills_collects_and_reconciles_reporting_counts_only(
    configured: DataRoot,
    fake_account: FakeServer,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    fake_account.put(HOME, "recent", salted("recent", ics_time(days_ago(3))))
    fake_account.put(HOME, "old", salted("old", ics_time(days_ago(90))))

    assert main(["capture", "--backfill", days_ago(60).isoformat()]) == 0
    fake_account.put(FAMILY, "soon", salted("soon", ics_time(days_ago(-2))))
    assert main(["capture"]) == 0
    fake_account.delete(HOME, "recent", unreported=True)
    assert main(["capture", "--reconcile"]) == 0

    output = capsys.readouterr()
    assert output.out.count("icloud_calendar: collected 1") == 3
    assert len(versions(configured)) == 3
    state = configured.state / "icloud_calendar" / "calendars.json"
    assert state.stat().st_mode & 0o777 == 0o600
    for text in (SALT, SALTED_UID, "p01-caldav"):
        assert text not in output.out + output.err + caplog.text
        assert text not in configured.status.read_text()
        assert not any(text in str(path) for path in configured.path.rglob("*"))
    # The state holds hrefs, but the bytes it keeps aren't greppable.
    assert SALT not in state.read_text()


def test_a_backfill_doesnt_reconcile(configured: DataRoot, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        main(["capture", "--backfill", "2026-08-01", "--reconcile"])

    assert "not allowed with argument" in capsys.readouterr().err


def test_a_missing_app_specific_password_fails_the_calendar_alone(
    configured: DataRoot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("PA_ICLOUD_USERNAME", "argus@example.com")
    monkeypatch.setenv("PA_ICLOUD_PASSWORD_FILE", str(tmp_path / "config" / "icloud-password"))
    vault = tmp_path / "vault"
    (vault / "Daily").mkdir(parents=True)
    (vault / "Daily" / "2020-01-01.md").write_text("## Errands\nPicked up the bikes.\n")
    monkeypatch.setenv("PA_VAULT_DIR", str(vault))
    monkeypatch.setenv("PA_VAULT_DAILY_NOTES", "Daily")

    assert main(["capture", "--backfill", "2019-12-01"]) == 1

    output = capsys.readouterr()
    assert "icloud_calendar: collection failed: There's no app-specific password at" in output.err
    status = read_status(configured.status)
    assert status.sources[Source.ICLOUD_CALENDAR].watermark is None
    assert status.sources[Source.OBSIDIAN].watermark is not None
