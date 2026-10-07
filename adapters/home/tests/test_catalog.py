import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.catalog import Catalog, CatalogEntry
from pa_core.envelope import Kind, Participant, Role, Source
from pa_home.cli import main

EMAIL_ID = "gmail_9ff7394d0df8584d"


def email(
    message_id: str = "wedding-0001@example.net",
    date: str = "Wed, 30 Sep 2026 21:30:00 -0500",
    sender: str = "David Kim <dkim@example.net>",
    cc: str = "Mara McNevans <mara@example.com>",
) -> bytes:
    return (
        f"Message-ID: <{message_id}>\r\n"
        f"Date: {date}\r\n"
        f"From: {sender}\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        f"Cc: {cc}\r\n"
        "Subject: Hotel block\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        "The hotel block closes on Oct 9.\r\n"
    ).encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    return root


def drop_in_inbox(data_root: Path, name: str, payload: bytes) -> None:
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(payload)


def open_catalog(data_root: Path) -> Catalog:
    return Catalog(data_root / "catalog" / "catalog.sqlite")


def test_an_ingested_email_is_in_the_catalog_with_its_fields(data_root: Path):
    drop_in_inbox(data_root, "hotel.eml", email())

    assert main(["ingest"]) == 0

    envelope_file = Path("episodes/gmail/2026-10") / f"{EMAIL_ID}.json"
    stored_hash = json.loads((data_root / "l0" / envelope_file).read_bytes())["content_hash"]
    with open_catalog(data_root) as catalog:
        assert catalog.get(EMAIL_ID) == CatalogEntry(
            episode_id=EMAIL_ID,
            source=Source.GMAIL,
            kind=Kind.EMAIL,
            occurred_start=datetime(2026, 10, 1, 2, 30, tzinfo=UTC),
            occurred_end=None,
            participants=(
                Participant(identifier="dkim@example.net", name="David Kim", role=Role.SENDER),
                Participant(
                    identifier="argus@example.com", name="Argus McNevans", role=Role.RECIPIENT
                ),
                Participant(identifier="mara@example.com", name="Mara McNevans", role=Role.CC),
            ),
            calendar_name=None,
            content_hash=stored_hash,
            episode_path=str(envelope_file),
            raw_path=f"raw/gmail/2026-10/{EMAIL_ID}.eml",
        )


def calendar_event(
    uid: str = "swim-lessons-0001@icloud.com",
    sequence: int = 0,
    start: str = "20261017T090000",
    end: str = "20261017T100000",
    calendar: str = "Family",
    attendee: str = "mailto:mara@example.com",
) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"X-WR-CALNAME:{calendar}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        "DTSTAMP:20261001T140000Z",
        f"DTSTART;TZID=America/Chicago:{start}",
        f"DTEND;TZID=America/Chicago:{end}",
        "SUMMARY:Swim lessons",
        "ORGANIZER;CN=Argus McNevans:mailto:argus@example.com",
        f"ATTENDEE:{attendee}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(f"{line}\r\n" for line in lines).encode()


def daily_note(day: str = "2026-10-05", text: str = "## Swim lessons\n\nMara prefers weekdays.\n"):
    return f"Vault-Path: Daily/{day}.md\n\n{text}".encode()


def ingest_a_bit_of_everything(data_root: Path) -> None:
    drop_in_inbox(data_root, "hotel.eml", email())
    drop_in_inbox(data_root, "swim.ics", calendar_event())
    drop_in_inbox(data_root, "2026-10-05.md", daily_note())
    assert main(["ingest"]) == 0
    drop_in_inbox(
        data_root,
        "swim.ics",
        calendar_event(sequence=1, start="20261017T100000", end="20261017T110000"),
    )
    drop_in_inbox(data_root, "2026-10-05.md", daily_note(text="## Swim lessons\n\nSaturdays.\n"))
    assert main(["ingest"]) == 0


def all_entries(data_root: Path) -> list[CatalogEntry]:
    with open_catalog(data_root) as catalog:
        return catalog.entries()


def test_a_rebuilt_catalog_has_the_same_contents_as_the_one_ingest_built(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    ingest_a_bit_of_everything(data_root)
    built_by_ingest = all_entries(data_root)
    capsys.readouterr()

    assert main(["catalog", "rebuild"]) == 0

    assert all_entries(data_root) == built_by_ingest
    assert len(built_by_ingest) == 5
    assert capsys.readouterr().out == "catalog rebuilt: 5 episodes\n"


def test_deleting_the_catalog_and_rebuilding_it_loses_nothing(data_root: Path):
    ingest_a_bit_of_everything(data_root)
    before = all_entries(data_root)
    (data_root / "catalog" / "catalog.sqlite").unlink()

    assert main(["catalog", "rebuild"]) == 0

    assert all_entries(data_root) == before


def test_ingest_rebuilds_a_deleted_catalog_before_adding_new_episodes(data_root: Path):
    ingest_a_bit_of_everything(data_root)
    before = all_entries(data_root)
    (data_root / "catalog" / "catalog.sqlite").unlink()

    drop_in_inbox(data_root, "shuttle.eml", email(message_id="wedding-0002@example.net"))
    assert main(["ingest"]) == 0

    after = all_entries(data_root)
    assert [entry for entry in after if entry in before] == before
    assert len(after) == len(before) + 1


def test_an_episode_that_landed_but_was_not_cataloged_is_cataloged_when_reingested(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    catalog_file = data_root / "catalog" / "catalog.sqlite"
    assert main(["catalog", "rebuild"]) == 0
    catalog_file.chmod(0o444)
    drop_in_inbox(data_root, "hotel.eml", email())

    with pytest.raises(sqlite3.OperationalError):
        main(["ingest"])

    assert (data_root / "inbox" / "hotel.eml").exists()
    catalog_file.chmod(0o644)
    capsys.readouterr()
    assert main(["ingest"]) == 0

    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"
    with open_catalog(data_root) as catalog:
        assert catalog.get(EMAIL_ID) is not None


def when_and_what(entries: list[CatalogEntry]) -> list[tuple[datetime, Kind, str | None]]:
    return [(entry.occurred_start, entry.kind, entry.calendar_name) for entry in entries]


def test_looking_up_a_participant_finds_their_episodes_in_time_order(data_root: Path):
    ingest_a_bit_of_everything(data_root)

    with open_catalog(data_root) as catalog:
        mara = catalog.with_participant("Mara@Example.com")
        david = catalog.with_participant("dkim@example.net")
        nobody = catalog.with_participant("swim-instructor@example.com")

    assert when_and_what(mara) == [
        (datetime(2026, 10, 1, 2, 30, tzinfo=UTC), Kind.EMAIL, None),
        (datetime(2026, 10, 17, 14, 0, tzinfo=UTC), Kind.CALENDAR_EVENT, "Family"),
        (datetime(2026, 10, 17, 15, 0, tzinfo=UTC), Kind.CALENDAR_EVENT, "Family"),
    ]
    assert mara[1].occurred_end == datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
    assert [entry.episode_id for entry in david] == [EMAIL_ID]
    assert nobody == []
