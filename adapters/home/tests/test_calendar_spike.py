"""The calendar spike (#85): the structure of the owner's real calendars, read over CalDAV, and
never their content.

The spike runs here against a fake calendar server holding synthetic resources salted with
distinctive strings, standing in for real summaries, descriptions, locations, attendees, UIDs,
hrefs, etags, sync tokens, timestamps, and the credential. None of them may appear in anything
the spike prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pa_home.calendar_spike import main
from pa_home.collectors.calendar_server import (
    Changes,
    Collection,
    Listing,
    Resource,
    SyncTokenRejected,
)
from pa_home.config import ICloudCredentials

APPLE_ID = "zq.hush@quackmail.example"
APP_PASSWORD = "wxyz-quil-lonv-asqu"
SUMMARY = "Clandestine Pumpkin Summit"
OTHER_SUMMARY = "Marmalade vault inspection"
MARKER = "PA spike test"
DESCRIPTION = "Bring the periwinkle ledger."
LOCATION = "Snorkel Hall, 42 Wobble Lane"
ATTENDEE = "mailto:periwinkle.otter@burrow.example"
UID_PREFIX = "turnip-uid"
HREF_PREFIX = "/9988776655/calendars/moonwhistle/"
ETAG_PREFIX = "etag-gooseberry"
TOKEN_PREFIX = "https://www.icloud.com/sync/tok-bellwether"
SALT = (
    APPLE_ID,
    "quackmail",
    APP_PASSWORD,
    SUMMARY,
    "Pumpkin",
    OTHER_SUMMARY,
    "Marmalade",
    DESCRIPTION,
    "periwinkle",
    LOCATION,
    "Snorkel",
    "Wobble",
    "burrow",
    UID_PREFIX,
    HREF_PREFIX,
    "9988776655",
    "moonwhistle",
    ETAG_PREFIX,
    "gooseberry",
    "bellwether",
    "icloud.com",
    "2031",
    "Europe/Tallinn",
)


def vevent(
    uid: str,
    summary: str = SUMMARY,
    *,
    start: str = "20310105T090000",
    rrule: bool = False,
    exdates: int = 0,
    rdate: bool = False,
    recurrence_id: str | None = None,
    sequence: int | None = None,
    last_modified: str | None = None,
) -> str:
    """A VEVENT, salted throughout."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:{UID_PREFIX}-{uid}",
        "DTSTAMP:20310101T000000Z",
        f"DTSTART;TZID=Europe/Tallinn:{start}",
        f"DTEND;TZID=Europe/Tallinn:{start[:-6]}100000",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{DESCRIPTION}",
        f"LOCATION:{LOCATION}",
        f"ATTENDEE;CN=Periwinkle Otter:{ATTENDEE}",
    ]
    if recurrence_id:
        lines.append(f"RECURRENCE-ID;TZID=Europe/Tallinn:{recurrence_id}")
    if rrule:
        lines.append("RRULE:FREQ=WEEKLY;COUNT=10")
    lines += [f"EXDATE;TZID=Europe/Tallinn:203101{12 + 7 * n}T090000" for n in range(exdates)]
    if rdate:
        lines.append("RDATE;TZID=Europe/Tallinn:20310301T090000")
    if sequence is not None:
        lines.append(f"SEQUENCE:{sequence}")
    if last_modified:
        lines.append(f"LAST-MODIFIED:{last_modified}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def ics(*events: str, timezone: bool = True, calname: bool = False) -> bytes:
    """A calendar resource holding `events`, as CalDAV serves it."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Apple Inc.//iCloud 2031//EN"]
    if calname:
        lines.append(f"X-WR-CALNAME:{OTHER_SUMMARY}")
    if timezone:
        lines += [
            "BEGIN:VTIMEZONE",
            "TZID:Europe/Tallinn",
            "BEGIN:STANDARD",
            "DTSTART:20311030T040000",
            "TZOFFSETFROM:+0300",
            "TZOFFSETTO:+0200",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    lines += [*events, "END:VCALENDAR", ""]
    return "\r\n".join(lines).encode()


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

    def add_calendar(
        self,
        name: str | None,
        components: tuple[str, ...] = ("VEVENT",),
        kinds: tuple[str, ...] = (),
        writable: bool | None = True,
    ) -> str:
        url = f"https://p01-caldav.icloud.com{HREF_PREFIX}{len(self.calendars)}/"
        collection = Collection(url, name, components, kinds, writable)
        self.calendars[url] = FakeCalendar(collection)
        return url

    def put(
        self,
        calendar: str,
        item: str,
        data: bytes,
        *,
        keep_etag: bool = False,
        unreported: bool = False,
    ) -> None:
        """Adds or replaces a resource. A server may keep its etag, or leave the change out of
        its sync reports."""
        self.token += 1
        href = f"{calendar}{item}.ics"
        resources = self.calendars[calendar].resources
        etag = resources[href].etag if keep_etag else f'"{ETAG_PREFIX}-{self.token}"'
        resources[href] = Resource(href, etag, data)
        if not unreported:
            self.calendars[calendar].history.append((href, self.token))

    def delete(self, calendar: str, item: str) -> None:
        self.token += 1
        href = f"{calendar}{item}.ics"
        del self.calendars[calendar].resources[href]
        self.calendars[calendar].history.append((href, self.token))

    def collections(self) -> list[Collection]:
        return [calendar.collection for calendar in self.calendars.values()]

    def listing(self, collection: Collection) -> Listing:
        calendar = self.calendars[collection.url]
        return Listing(self._token(), list(calendar.resources.values()))

    def changes(self, collection: Collection, sync_token: str) -> Changes:
        since = int(sync_token.rpartition("-")[2])
        if since < self.earliest_token:
            raise SyncTokenRejected(403)
        calendar = self.calendars[collection.url]
        touched = {href for href, token in calendar.history if token > since}
        changed = {
            href: calendar.resources[href].etag for href in touched if href in calendar.resources
        }
        removed = [href for href in touched if href not in calendar.resources]
        return Changes(self._token(), changed, removed)

    def _token(self) -> str:
        return f"{TOKEN_PREFIX}-{self.token}"


@dataclass
class Spike:
    server: FakeServer
    data_root: Path
    capsys: pytest.CaptureFixture[str]
    connected_with: list[ICloudCredentials] = field(default_factory=list)

    def run(self, *argv: str, expect: int = 0) -> str:
        """What the spike prints for `argv`, once it's shown to hold none of the salt."""

        def connect(credentials: ICloudCredentials) -> FakeServer:
            self.connected_with.append(credentials)
            return self.server

        assert main(list(argv), connect=connect) == expect
        captured = self.capsys.readouterr()
        output = captured.out + captured.err
        leaked = [salt for salt in SALT if salt.lower() in output.lower()]
        assert leaked == [], output
        return output


@pytest.fixture
def spike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Spike:
    password = tmp_path / "config" / "icloud-app-password"
    password.parent.mkdir()
    password.write_text(APP_PASSWORD + "\n")
    password.chmod(0o600)
    data_root = tmp_path / "pa-data"
    data_root.mkdir()
    monkeypatch.setenv("PA_ICLOUD_USERNAME", APPLE_ID)
    monkeypatch.setenv("PA_ICLOUD_PASSWORD_FILE", str(password))
    monkeypatch.setenv("PA_DATA_DIR", str(data_root))
    return Spike(FakeServer(), data_root, capsys)


def test_calendars_lists_each_calendar_by_name_with_its_kind_and_whether_birthdays_are_served(
    spike: Spike,
):
    spike.server.add_calendar("Home")
    spike.server.add_calendar("Family", kinds=("shared",), writable=False)
    spike.server.add_calendar("Reminders", components=("VTODO",))
    spike.server.add_calendar(None)

    output = spike.run("calendars")

    assert output == (
        "calendars: 4\n"
        "  1. Home: VEVENT, writable\n"
        "  2. Family: VEVENT, shared, read-only\n"
        "  3. Reminders: VTODO, writable\n"
        "  4. (no display name): VEVENT, writable\n"
        "birthdays calendar: not served\n"
    )
    assert [(c.username, c.password) for c in spike.connected_with] == [(APPLE_ID, APP_PASSWORD)]


def test_calendars_prints_only_the_component_and_resource_types_caldav_defines(spike: Spike):
    spike.server.add_calendar(
        "Home", components=("VEVENT", "Moonwhistle"), kinds=("shared-owner", "Bellwether")
    )

    output = spike.run("calendars")

    assert "  1. Home: VEVENT/1 other, shared-owner, 1 other kind, writable\n" in output


class FailingServer(FakeServer):
    def collections(self) -> list[Collection]:
        raise ConnectionError(f"{TOKEN_PREFIX}{HREF_PREFIX} refused {APPLE_ID}")


def test_an_unexpected_failure_is_reported_by_its_type_alone(spike: Spike):
    spike.server = FailingServer()

    output = spike.run("calendars", expect=1)

    assert output == "calendar-spike: unexpected ConnectionError\n"


def test_calendars_says_when_a_birthdays_calendar_is_served(spike: Spike):
    spike.server.add_calendar("Home")
    spike.server.add_calendar("Birthdays", writable=False)

    output = spike.run("calendars")

    assert output.endswith("birthdays calendar: served (Birthdays)\n")


def every_shape(server: FakeServer) -> str:
    """A calendar holding one resource of each shape a CalDAV calendar can serve."""
    home = server.add_calendar("Home")
    server.put(home, "single", ics(vevent("a", sequence=0, last_modified="20310102T101010Z")))
    server.put(home, "series", ics(vevent("b", rrule=True, exdates=2, sequence=1)))
    server.put(
        home,
        "overridden",
        ics(
            vevent("c", rrule=True, exdates=1, last_modified="20310102T101010Z"),
            vevent("c", recurrence_id="20310112T090000", sequence=2),
            vevent("c", recurrence_id="20310119T090000"),
        ),
    )
    server.put(home, "orphan", ics(vevent("d", recurrence_id="20310112T090000")))
    server.put(home, "rdate", ics(vevent("e", rdate=True), timezone=False, calname=True))
    server.put(home, "two-uids", ics(vevent("f"), vevent("g")))
    server.put(home, "unreadable", f"BEGIN:VCALENDAR\r\nSUMMARY:{SUMMARY}\r\n".encode())
    return home


def test_a_snapshot_reports_each_calendars_shapes_as_counts(spike: Spike):
    every_shape(spike.server)

    output = spike.run("snapshot", "baseline")

    assert output == (
        "snapshot baseline: 1 calendar\n"
        "calendar Home\n"
        "  resources: 7 (sync token: given; etags: 7 of 7)\n"
        "  unreadable: 1\n"
        "  events per resource: 1 event: 4, 2 events: 1, 3 events: 1\n"
        "  shapes:\n"
        "    single event: 1\n"
        "    series: 2\n"
        "    series with overrides: 1 (overrides: 2 in 1)\n"
        "    overrides without their series: 1\n"
        "    several UIDs: 1\n"
        "  resources with RRULE: 2, EXDATE: 2, RDATE: 1\n"
        "  resources with VTIMEZONE: 5, X-WR-CALNAME: 1\n"
        "  events: 9, with SEQUENCE: 3, LAST-MODIFIED: 2\n"
        "  other components: none\n"
    )


def test_a_snapshot_of_an_empty_calendar_says_it_holds_nothing(spike: Spike):
    spike.server.add_calendar("Birthdays", writable=False)

    output = spike.run("snapshot", "baseline")

    assert (
        "  resources: 0 (sync token: given; etags: 0 of 0)\n"
        "  events per resource: none\n"
        "  shapes: none\n"
    ) in output


def test_a_snapshot_keeps_each_resource_as_served_in_scratch_under_the_data_root(spike: Spike):
    home = every_shape(spike.server)

    spike.run("snapshot", "baseline")

    scratch = spike.data_root / "scratch" / "calendar-spike"
    kept = sorted(path.read_bytes() for path in (scratch / "baseline").rglob("*.ics"))
    served = sorted(resource.data for resource in spike.server.calendars[home].resources.values())
    assert kept == served
    assert sorted(path.name for path in spike.data_root.iterdir()) == ["scratch"]
    assert (scratch.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o077) == 0 for path in scratch.rglob("*") if path.is_file())


def test_a_snapshot_takes_the_named_calendars_else_every_calendar_of_events(spike: Spike):
    spike.server.add_calendar("Home")
    spike.server.add_calendar("Family", kinds=("shared",))
    spike.server.add_calendar("Reminders", components=("VTODO",))

    assert "calendar Family\n" in spike.run("snapshot", "family", "--calendar", "Family")
    assert "calendar Home\n" not in spike.run("snapshot", "named", "--calendar", "Family")
    every = spike.run("snapshot", "every")
    assert "snapshot every: 2 calendars\ncalendar Home\n" in every
    assert "calendar Family\n" in every
    assert "Reminders" not in every


def test_a_snapshot_refuses_a_calendar_the_account_doesnt_serve(spike: Spike):
    spike.server.add_calendar("Home")

    output = spike.run("snapshot", "baseline", "--calendar", "Work", expect=1)

    assert "no calendar is named Work" in output
    assert not (spike.data_root / "scratch" / "calendar-spike" / "baseline").exists()


@pytest.mark.parametrize("label", ["../escape", "a/b", ".hidden", ""])
def test_a_snapshot_label_is_a_plain_name(spike: Spike, label: str):
    spike.server.add_calendar("Home")

    spike.run("snapshot", label, expect=1)

    assert not (spike.data_root / "scratch").exists() or not any(
        path.name != "calendar-spike" for path in (spike.data_root / "scratch").iterdir()
    )


def test_a_snapshot_never_creates_the_data_root(spike: Spike):
    spike.server.add_calendar("Home")
    spike.data_root.rmdir()

    assert "doesn't exist" in spike.run("snapshot", "baseline", expect=1)
    assert not spike.data_root.exists()


def test_a_snapshot_never_replaces_another(spike: Spike):
    spike.server.add_calendar("Home")
    spike.run("snapshot", "baseline")

    assert "already exists" in spike.run("snapshot", "baseline", expect=1)


# The owner's test event, a weekly series titled with the marker, through each change the owner
# makes to it.


def marker_series(*overrides: str, start: str = "20310105T090000", exdates: int = 0, **bump):
    """The test event: a weekly series with the marker as its SUMMARY, and its overrides."""
    version = {"sequence": 0, "last_modified": "20310103T080000Z"} | bump
    return ics(
        vevent("test", MARKER, start=start, rrule=True, exdates=exdates, **version), *overrides
    )


def changed_occurrence(sequence: int | None = None) -> str:
    """An override of the test event: its second occurrence, moved to the afternoon."""
    return vevent(
        "test", MARKER, recurrence_id="20310112T090000", start="20310112T150000", sequence=sequence
    )


@pytest.fixture
def home(spike: Spike) -> str:
    """The owner's calendar, holding resources of every shape, snapshotted as the baseline."""
    home = every_shape(spike.server)
    spike.run("snapshot", "baseline")
    return home


def test_a_diff_reports_the_test_event_created_and_the_sync_report_agreeing(
    spike: Spike, home: str
):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")

    output = spike.run("diff", "baseline", "created", "--marker", MARKER)

    assert output.startswith(
        "diff baseline created\n"
        "calendar Home\n"
        "  listing: added 1, changed 0, removed 0, unchanged 7\n"
        "  sync token: changed\n"
        "  sync report from baseline's token, taken with created: changed 1, removed 0\n"
        "  sync report agrees with the listing: yes\n"
        "test event (SUMMARY is the marker): in baseline 0, in created 1\n"
        "  added in created, calendar Home\n"
        "  events: 1 (series), EXDATEs: 0, with SEQUENCE: 1, LAST-MODIFIED: 1\n"
        "  structure in created:\n"
        "    format: calendar\n"
    )


def test_a_diff_reports_whether_moving_the_series_bumped_its_version(spike: Spike, home: str):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")
    spike.server.put(
        home,
        "test",
        marker_series(start="20310106T090000", sequence=1, last_modified="20310104T080000Z"),
    )
    spike.run("snapshot", "moved")

    output = spike.run("diff", "created", "moved", "--marker", MARKER)

    assert (
        "  listing: added 0, changed 1, removed 0, unchanged 7\n"
        "  changed: etag new 1, bytes new under the same etag 0\n"
    ) in output
    assert (
        "test event (SUMMARY is the marker): in created 1, in moved 1\n"
        "  changed in place, calendar Home; etag new\n"
        "  events: 1 -> 1 (series -> series), EXDATEs: 0 -> 0\n"
        "  content apart from DTSTAMP: changed; version (every event's SEQUENCE and "
        "LAST-MODIFIED): changed\n"
        "  series: changed (DTSTART changed, RRULE unchanged); SEQUENCE raised, LAST-MODIFIED "
        "later\n"
        "  overrides: added 0, removed 0, changed 0 of 0\n"
        "  changed events without a SEQUENCE or LAST-MODIFIED bump: 0\n"
    ) in output


def test_a_diff_reports_a_changed_occurrence_and_a_change_that_bumped_nothing(
    spike: Spike, home: str
):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")
    spike.server.put(home, "test", marker_series(changed_occurrence()))
    spike.run("snapshot", "one-changed")

    output = spike.run("diff", "created", "one-changed", "--marker", MARKER)

    assert (
        "  events: 1 -> 2 (series -> series with overrides), EXDATEs: 0 -> 0\n"
        "  content apart from DTSTAMP: changed; version (every event's SEQUENCE and "
        "LAST-MODIFIED): changed\n"
        "  series: unchanged; SEQUENCE unchanged, LAST-MODIFIED unchanged\n"
        "  overrides: added 1 (with SEQUENCE: 0, LAST-MODIFIED: 0), removed 0, changed 0 of 0\n"
    ) in output


def test_a_diff_reports_a_removed_occurrence_as_an_exdate(spike: Spike, home: str):
    spike.server.put(home, "test", marker_series(changed_occurrence(sequence=1)))
    spike.run("snapshot", "one-changed")
    spike.server.put(home, "test", marker_series(changed_occurrence(sequence=1), exdates=1))
    spike.run("snapshot", "one-removed")

    output = spike.run("diff", "one-changed", "one-removed", "--marker", MARKER)

    assert (
        "  events: 2 -> 2 (series with overrides -> series with overrides), EXDATEs: 0 -> 1\n"
        "  content apart from DTSTAMP: changed; version (every event's SEQUENCE and "
        "LAST-MODIFIED): unchanged\n"
        "  series: changed (DTSTART unchanged, RRULE unchanged); SEQUENCE unchanged, "
        "LAST-MODIFIED unchanged\n"
        "  overrides: added 0, removed 0, changed 0 of 1\n"
        "  changed events without a SEQUENCE or LAST-MODIFIED bump: 1\n"
    ) in output


def test_a_diff_reports_the_test_event_deleted_and_whether_the_sync_report_said_so(
    spike: Spike, home: str
):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")
    spike.server.delete(home, "test")
    spike.run("snapshot", "deleted")

    output = spike.run("diff", "created", "deleted", "--marker", MARKER)

    assert "  listing: added 0, changed 0, removed 1, unchanged 7\n" in output
    assert "taken with deleted: changed 0, removed 1\n" in output
    assert "  sync report agrees with the listing: yes\n" in output
    assert (
        "test event (SUMMARY is the marker): in created 1, in deleted 0\n"
        "  removed in deleted, calendar Home\n"
    ) in output


def test_a_diff_between_distant_snapshots_takes_a_sync_report_now(spike: Spike, home: str):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")
    spike.server.delete(home, "test")
    spike.run("snapshot", "deleted")

    output = spike.run("diff", "baseline", "deleted")

    assert "  listing: added 0, changed 0, removed 0, unchanged 7\n" in output
    assert "  sync report from baseline's token, taken now: changed 0, removed 1\n" in output
    # RFC 6578 lets a server report removed a resource the client never saw.
    assert (
        "  sync report agrees with the listing: yes (also reported removed, never listed: 1)\n"
    ) in output
    assert "test event" not in output


def test_a_diff_reports_a_sync_token_the_server_refuses(spike: Spike, home: str):
    spike.server.put(home, "test", marker_series())
    spike.run("snapshot", "created")
    spike.server.delete(home, "test")
    spike.server.earliest_token = spike.server.token
    spike.run("snapshot", "deleted")

    output = spike.run("diff", "baseline", "deleted")
    stored = spike.run("diff", "created", "deleted")

    assert "  sync report from baseline's token, taken now: refused (HTTP 403)\n" in output
    assert "  sync report from created's token, taken with deleted: refused (HTTP 403)\n" in (
        stored
    )


def test_a_diff_reports_changes_the_sync_report_missed_and_etags_that_didnt_move(
    spike: Spike, home: str
):
    spike.server.put(home, "single", ics(vevent("a", OTHER_SUMMARY)), keep_etag=True)
    spike.server.put(home, "series", ics(vevent("b", OTHER_SUMMARY)), unreported=True)
    spike.run("snapshot", "edited")

    output = spike.run("diff", "baseline", "edited", "--marker", MARKER)

    assert (
        "  listing: added 0, changed 2, removed 0, unchanged 5\n"
        "  changed: etag new 1, bytes new under the same etag 1\n"
    ) in output
    assert (
        "  sync report agrees with the listing: no (changed in the listing, not reported: 1)\n"
    ) in output
    assert "test event (SUMMARY is the marker): in baseline 0, in edited 0\n" in output


def test_a_diff_needs_both_snapshots(spike: Spike, home: str):
    assert "no snapshot missing" in spike.run("diff", "baseline", "missing", expect=1)


def test_clean_deletes_every_snapshot_and_nothing_else(
    spike: Spike, home: str, monkeypatch: pytest.MonkeyPatch
):
    spike.run("snapshot", "again")
    (spike.data_root / "scratch" / "other").mkdir()
    monkeypatch.delenv("PA_ICLOUD_USERNAME")

    assert spike.run("clean") == "clean: deleted 2 snapshots\n"
    assert sorted(path.name for path in (spike.data_root / "scratch").iterdir()) == ["other"]
    assert spike.run("clean") == "clean: nothing to delete\n"
