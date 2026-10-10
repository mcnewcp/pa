import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pa_core.catalog import SqliteCatalog
from pa_core.ingest import ingest
from pa_home.cli import main
from pa_home.config import load_owner
from pa_home.filesystem_l0 import FilesystemL0

# Sent late on 30 Sep in Chicago, which is already October in UTC.
EPISODE_ID = "gmail_9ff7394d0df8584d"
EPISODE_FILE = Path("l0/episodes/gmail/2026-10") / f"{EPISODE_ID}.json"
RAW_FILE = Path("l0/raw/gmail/2026-10") / f"{EPISODE_ID}.eml"


def email(
    labels: str = "Inbox",
    body: str = "The hotel block closes on Oct 9.",
    message_id: str = "wedding-0001@example.net",
) -> bytes:
    return (
        f"X-Gmail-Labels: {labels}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "Date: Wed, 30 Sep 2026 21:30:00 -0500\r\n"
        "From: David Kim <dkim@example.net>\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        "Subject: Hotel block\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com, Argus.McNevans@example.org")
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", "Gus")
    return root


def drop_in_inbox(data_root: Path, name: str, payload: bytes) -> None:
    """Write `payload` at `name`, a path within the inbox such as `gmail/hotel.eml`."""
    target = data_root / "inbox" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def files_under(path: Path) -> list[Path]:
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def inbox_files(data_root: Path) -> list[Path]:
    return files_under(data_root / "inbox")


def snapshot(directory: Path) -> dict[Path, bytes]:
    return {path: (directory / path).read_bytes() for path in files_under(directory)}


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


MALFORMED = Path(__file__).parent / "fixtures" / "malformed"


def drop_fixture_in_inbox(data_root: Path, name: str) -> bytes:
    """Drop a malformed fixture, all of which are mail, into the gmail directory."""
    payload = (MALFORMED / name).read_bytes()
    drop_in_inbox(data_root, f"gmail/{name}", payload)
    return payload


@dataclass
class Quarantined:
    raw: bytes
    record: dict[str, Any]


def quarantined(data_root: Path) -> list[Quarantined]:
    """Each quarantined item: its raw bytes and its record, which sits beside it as `.json`.

    A raw payload may itself be `.json`, so a record is a file with a raw payload beside it.
    """
    quarantine = data_root / "l0" / "quarantine"
    if not quarantine.exists():
        return []
    files = sorted(quarantine.iterdir())
    return [
        Quarantined(
            raw=record_file.with_suffix("").read_bytes(),
            record=json.loads(record_file.read_bytes()),
        )
        for record_file in files
        if record_file.suffix == ".json" and record_file.with_suffix("") in files
    ]


def test_ingested_email_lands_in_l0_as_a_read_only_envelope_and_raw_payload(data_root: Path):
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) == 0

    assert files_under(data_root / "l0") == [
        Path("episodes/gmail/2026-10") / f"{EPISODE_ID}.json",
        Path("raw/gmail/2026-10") / f"{EPISODE_ID}.eml",
    ]
    assert (data_root / RAW_FILE).read_bytes() == email()
    assert is_read_only(data_root / RAW_FILE)
    assert is_read_only(data_root / EPISODE_FILE)


def test_inbox_is_empty_after_ingest_and_the_summary_is_printed(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    main(["ingest"])

    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_reingesting_the_same_email_adds_nothing_and_reports_it_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    main(["ingest"])
    before = snapshot(data_root / "l0")
    capsys.readouterr()

    drop_in_inbox(data_root, "gmail/hotel-again.eml", email())
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0") == before
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"


def test_a_recapture_that_only_changes_labels_is_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "gmail/hotel.eml", email(labels="Inbox"))
    main(["ingest"])
    capsys.readouterr()

    drop_in_inbox(data_root, "gmail/hotel.eml", email(labels="Archived,Wedding"))
    main(["ingest"])

    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"
    assert (data_root / RAW_FILE).read_bytes() == email(labels="Inbox")
    assert quarantined(data_root) == []


REPOSITORY = Path(__file__).resolve().parents[3]


def test_commands_refuse_to_start_when_the_data_root_is_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("PA_DATA_DIR", raising=False)

    assert main(["ingest"]) != 0

    assert "PA_DATA_DIR is not set" in capsys.readouterr().err


def test_commands_refuse_to_start_when_the_data_root_is_inside_the_repository(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    inside = REPOSITORY / "pa-data-must-not-be-created"
    monkeypatch.setenv("PA_DATA_DIR", str(inside))

    assert main(["ingest"]) != 0

    assert "inside the repository" in capsys.readouterr().err
    assert not inside.exists()


def test_a_data_root_that_resolves_into_the_repository_through_a_symlink_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    link = tmp_path / "pa-data"
    link.symlink_to(REPOSITORY, target_is_directory=True)
    monkeypatch.setenv("PA_DATA_DIR", str(link))

    assert main(["ingest"]) != 0

    assert "inside the repository" in capsys.readouterr().err
    assert not (REPOSITORY / "inbox").exists()


def leave_raw_from_an_interrupted_ingest(data_root: Path, payload: bytes) -> Path:
    leftover = data_root / RAW_FILE
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(payload)
    leftover.chmod(0o444)
    return leftover


def test_a_raw_payload_left_by_an_interrupted_ingest_is_never_replaced(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    leftover = leave_raw_from_an_interrupted_ingest(data_root, email(body="Closes on Oct 2."))
    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    drop_in_inbox(data_root, "gmail/shuttle.eml", email(message_id="wedding-0002@example.net"))

    assert main(["ingest"]) == 0

    assert leftover.read_bytes() == email(body="Closes on Oct 2.")
    assert not (data_root / EPISODE_FILE).exists()
    [item] = quarantined(data_root)
    assert item.raw == email()
    assert item.record["inbox_name"] == "gmail/hotel.eml"
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


def test_a_labels_only_recapture_finishes_an_interrupted_ingest_from_its_leftover_raw(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    leftover = leave_raw_from_an_interrupted_ingest(data_root, email(labels="Inbox"))
    drop_in_inbox(data_root, "gmail/hotel.eml", email(labels="Archived"))

    assert main(["ingest"]) == 0

    assert leftover.read_bytes() == email(labels="Inbox")
    episode = json.loads((data_root / EPISODE_FILE).read_bytes())
    assert episode["labels"] == ["Inbox"]
    assert quarantined(data_root) == []
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_an_interrupted_ingest_completes_when_the_payload_is_recaptured(data_root: Path):
    leave_raw_from_an_interrupted_ingest(data_root, email())
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) == 0

    assert (data_root / EPISODE_FILE).exists()


def test_commands_refuse_a_data_root_inside_the_repository_they_are_run_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PA_DATA_DIR", "data")

    assert main(["ingest"]) != 0

    assert "inside the repository" in capsys.readouterr().err
    assert not (checkout / "data").exists()


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("no_message_id.eml", "no Message-ID"),
        ("unparseable_date.eml", "no valid Date"),
        ("unknown_shape.txt", "inbox/gmail/ takes '.eml' payloads, not '.txt'"),
        ("unknown_charset.eml", "unknown encoding: x-nonexistent"),
        ("undecodable_sender.eml", "codec can't encode"),
    ],
)
def test_a_malformed_payload_is_quarantined_with_a_reason_and_the_rest_still_ingests(
    data_root: Path, capsys: pytest.CaptureFixture[str], fixture: str, reason: str
):
    broken = drop_fixture_in_inbox(data_root, fixture)
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == broken
    assert item.record["inbox_name"] == f"gmail/{fixture}"
    assert reason in item.record["reason"]
    assert datetime.fromisoformat(item.record["quarantined_at"]).tzinfo is not None
    assert (data_root / EPISODE_FILE).exists()
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


def test_a_recapture_with_different_content_is_quarantined_and_the_episode_is_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "gmail/hotel.eml", email(body="The hotel block closes on Oct 9."))
    main(["ingest"])
    episodes_before = snapshot(data_root / "l0" / "episodes")
    raw_before = snapshot(data_root / "l0" / "raw")
    capsys.readouterr()

    edited = email(body="The hotel block closes on Oct 2.")
    drop_in_inbox(data_root, "gmail/hotel.eml", edited)
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0" / "episodes") == episodes_before
    assert snapshot(data_root / "l0" / "raw") == raw_before
    [item] = quarantined(data_root)
    assert item.raw == edited
    assert item.record["inbox_name"] == "gmail/hotel.eml"
    assert EPISODE_ID in item.record["reason"]
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 0, quarantined 1\n"


def test_a_payload_stays_in_the_inbox_when_it_cannot_be_written_to_quarantine(data_root: Path):
    broken = drop_fixture_in_inbox(data_root, "no_message_id.eml")
    # A file where the quarantine directory belongs makes every quarantine write fail.
    (data_root / "l0").mkdir()
    (data_root / "l0" / "quarantine").write_bytes(b"")

    with pytest.raises(OSError):
        main(["ingest"])

    assert (data_root / "inbox" / "gmail" / "no_message_id.eml").read_bytes() == broken


def ingest_at(data_root: Path, moment: datetime):
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    with SqliteCatalog(data_root / "catalog" / "catalog.sqlite") as catalog:
        return ingest(
            inbox, FilesystemL0(data_root / "l0"), catalog, load_owner(), now=lambda: moment
        )


def test_a_recapture_that_only_changes_capture_time_is_unchanged(data_root: Path):
    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    ingest_at(data_root, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    before = snapshot(data_root / "l0")

    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    summary = ingest_at(data_root, datetime(2026, 10, 5, 8, 0, tzinfo=UTC))

    assert (summary.ingested, summary.unchanged, summary.quarantined) == (0, 1, 0)
    assert snapshot(data_root / "l0") == before


def test_the_same_payload_quarantined_twice_in_one_second_keeps_both_and_the_rest_ingests(
    data_root: Path,
):
    broken = drop_fixture_in_inbox(data_root, "no_message_id.eml")
    ingest_at(data_root, datetime(2026, 10, 1, 8, 0, 0, 100_000, tzinfo=UTC))

    drop_fixture_in_inbox(data_root, "no_message_id.eml")
    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    summary = ingest_at(data_root, datetime(2026, 10, 1, 8, 0, 0, 200_000, tzinfo=UTC))

    assert (summary.ingested, summary.unchanged, summary.quarantined) == (1, 0, 1)
    assert [item.raw for item in quarantined(data_root)] == [broken, broken]
    assert inbox_files(data_root) == []


def calendar_event(
    sequence: int = 0,
    last_modified: str = "20261001T135900Z",
    start: str = "20261017T090000",
    status: str = "CONFIRMED",
    dtstamp: str = "20261001T140000Z",
    people: tuple[str, ...] = (
        "ORGANIZER;CN=Argus McNevans:mailto:argus@example.com",
        "ATTENDEE;CN=Mara McNevans:mailto:mara@example.com",
    ),
) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "X-WR-CALNAME:Family",
        "BEGIN:VEVENT",
        "UID:swim-lessons-0001@icloud.com",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{dtstamp}",
        f"LAST-MODIFIED:{last_modified}",
        f"DTSTART;TZID=America/Chicago:{start}",
        "SUMMARY:Swim lessons",
        f"STATUS:{status}",
        *people,
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(f"{line}\r\n" for line in lines).encode()


def calendar_episodes(data_root: Path) -> list[dict[str, Any]]:
    episodes = data_root / "l0" / "episodes" / "icloud_calendar"
    return [json.loads(path.read_bytes()) for path in sorted(episodes.rglob("*.json"))]


def test_an_event_and_its_update_are_two_episodes_with_their_raw_payloads(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    original = calendar_event()
    moved = calendar_event(sequence=1, last_modified="20261005T120000Z", start="20261017T100000")
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", original)
    main(["ingest"])
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", moved)

    assert main(["ingest"]) == 0

    first, second = sorted(calendar_episodes(data_root), key=lambda e: e["occurred_at"]["start"])
    assert first["episode_id"] != second["episode_id"]
    assert first["thread_ref"] == second["thread_ref"]
    assert (first["occurred_at"]["start"], second["occurred_at"]["start"]) == (
        "2026-10-17T09:00:00-05:00",
        "2026-10-17T10:00:00-05:00",
    )
    assert (data_root / "l0" / first["raw_ref"]).read_bytes() == original
    assert (data_root / "l0" / second["raw_ref"]).read_bytes() == moved
    assert first["raw_ref"].startswith("raw/icloud_calendar/2026-10/")
    assert capsys.readouterr().out.splitlines()[-1] == "ingested 1, unchanged 0, quarantined 0"


def test_a_cancellation_is_its_own_episode_and_the_original_is_kept(data_root: Path):
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event())
    main(["ingest"])
    cancelled = calendar_event(sequence=1, last_modified="20261008T120000Z", status="CANCELLED")
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", cancelled)

    main(["ingest"])

    statuses = sorted(episode["body"] for episode in calendar_episodes(data_root))
    assert statuses == ["Status: cancelled", "Status: confirmed"]
    assert quarantined(data_root) == []


def test_the_envelope_carries_the_calendar_name_organizer_and_attendees(data_root: Path):
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event())

    main(["ingest"])

    [episode] = calendar_episodes(data_root)
    assert episode["calendar_name"] == "Family"
    assert episode["participants"] == [
        {"identifier": "argus@example.com", "name": "Argus McNevans", "role": "organizer"},
        {"identifier": "mara@example.com", "name": "Mara McNevans", "role": "attendee"},
    ]


def test_an_event_with_no_attendees_ingests_cleanly(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event(people=()))

    assert main(["ingest"]) == 0

    [episode] = calendar_episodes(data_root)
    assert episode["participants"] == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_reingesting_the_same_version_of_an_event_is_a_no_op(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event())
    main(["ingest"])
    before = snapshot(data_root / "l0")
    capsys.readouterr()

    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event())
    drop_in_inbox(
        data_root, "icloud_calendar/swim-reexported.ics", calendar_event(dtstamp="20261009T080000Z")
    )
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0") == before
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 2, quarantined 0\n"


def test_a_malformed_calendar_is_quarantined_with_a_reason(data_root: Path):
    broken = calendar_event().replace(b"UID:", b"X-UID:")
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", broken)

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == broken
    assert "no UID" in item.record["reason"]


def daily_note(text: str = "## Swim lessons\n\nMara prefers weekday evenings.\n") -> bytes:
    return f"Vault-Path: Daily/2026-10-05.md\n\n{text}".encode()


def note_episodes(data_root: Path) -> list[dict[str, Any]]:
    episodes = data_root / "l0" / "episodes" / "obsidian"
    return [json.loads(path.read_bytes()) for path in sorted(episodes.rglob("*.json"))]


def test_a_daily_note_ingests_authored_by_the_configured_owner_on_the_notes_day(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "obsidian/2026-10-05.md", daily_note())

    assert main(["ingest"]) == 0

    [episode] = note_episodes(data_root)
    assert episode["kind"] == "note"
    assert episode["participants"] == [
        {"identifier": "argus@example.com", "name": "Argus McNevans", "role": "author"}
    ]
    assert episode["occurred_at"] == {
        "start": "2026-10-05T00:00:00Z",
        "end": "2026-10-06T00:00:00Z",
    }
    assert episode["body"] == "## Swim lessons\n\nMara prefers weekday evenings."
    assert (data_root / "l0" / episode["raw_ref"]).read_bytes() == daily_note()
    assert episode["raw_ref"].startswith("raw/obsidian/2026-10/")
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_a_corrected_note_is_a_new_episode_in_the_same_thread_and_the_original_is_kept(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    original = daily_note("## Swim lessons\n\nMara prefers weekday evenings.\n")
    corrected = daily_note("## Swim lessons\n\nMara prefers Saturday mornings.\n")
    drop_in_inbox(data_root, "obsidian/2026-10-05.md", original)
    main(["ingest"])
    drop_in_inbox(data_root, "obsidian/2026-10-05.md", corrected)

    assert main(["ingest"]) == 0

    first, second = sorted(note_episodes(data_root), key=lambda e: "Saturday" in e["body"])
    assert first["episode_id"] != second["episode_id"]
    assert first["thread_ref"] == second["thread_ref"] == "Daily/2026-10-05.md"
    assert (data_root / "l0" / first["raw_ref"]).read_bytes() == original
    assert (data_root / "l0" / second["raw_ref"]).read_bytes() == corrected
    assert quarantined(data_root) == []
    assert capsys.readouterr().out.splitlines()[-1] == "ingested 1, unchanged 0, quarantined 0"


def test_reingesting_the_same_note_is_a_no_op(data_root: Path, capsys: pytest.CaptureFixture[str]):
    drop_in_inbox(data_root, "obsidian/2026-10-05.md", daily_note())
    main(["ingest"])
    before = snapshot(data_root / "l0")
    capsys.readouterr()

    drop_in_inbox(data_root, "obsidian/2026-10-05.md", daily_note())
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0") == before
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"


@pytest.mark.parametrize("unset", ["PA_OWNER_NAME", "PA_OWNER_EMAILS"])
def test_ingest_refuses_to_start_without_the_owner_configured(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unset: str
):
    monkeypatch.delenv(unset)
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) != 0

    error = capsys.readouterr().err
    assert "Owner configuration is missing" in error
    assert unset in error
    assert (data_root / "inbox" / "gmail" / "hotel.eml").read_bytes() == email()
    assert not (data_root / "l0").exists()


@pytest.mark.parametrize(
    ("name", "payload", "reason"),
    [
        (
            "icloud_calendar/hotel.eml",
            email(),
            "inbox/icloud_calendar/ takes '.ics' payloads, not '.eml'",
        ),
        ("gmail/swim.ics", calendar_event(), "inbox/gmail/ takes '.eml' payloads, not '.ics'"),
        ("obsidian/hotel.eml", email(), "inbox/obsidian/ takes '.md' payloads, not '.eml'"),
        ("gmail/2026-10-05.md", daily_note(), "inbox/gmail/ takes '.eml' payloads, not '.md'"),
    ],
)
def test_a_payload_in_another_sources_directory_is_quarantined_naming_directory_and_format(
    data_root: Path, capsys: pytest.CaptureFixture[str], name: str, payload: bytes, reason: str
):
    drop_in_inbox(data_root, name, payload)

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == payload
    assert item.record["inbox_name"] == name
    assert reason in item.record["reason"]
    assert not (data_root / "l0" / "episodes").exists()
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 0, quarantined 1\n"


def test_an_email_disguised_as_a_calendar_event_never_becomes_an_email(data_root: Path):
    drop_in_inbox(data_root, "icloud_calendar/hotel.ics", email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == email()
    assert item.record["inbox_name"] == "icloud_calendar/hotel.ics"
    assert not (data_root / "l0" / "episodes").exists()


@pytest.mark.parametrize(
    ("name", "payload"),
    [("hotel.eml", email()), ("swim.ics", calendar_event()), ("2026-10-05.md", daily_note())],
)
def test_any_payload_at_the_inbox_root_is_quarantined_and_the_rest_still_ingests(
    data_root: Path, capsys: pytest.CaptureFixture[str], name: str, payload: bytes
):
    drop_in_inbox(data_root, name, payload)
    drop_in_inbox(data_root, "gmail/shuttle.eml", email(message_id="wedding-0002@example.net"))

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == payload
    assert item.record["inbox_name"] == name
    assert "inbox root" in item.record["reason"]
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


@pytest.mark.parametrize("name", ["outlook/hotel.eml", "gmail/archive/hotel.eml"])
def test_a_payload_outside_a_source_directory_is_quarantined(data_root: Path, name: str):
    drop_in_inbox(data_root, name, email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.record["inbox_name"] == name
    assert "is not a source directory" in item.record["reason"]
    assert not (data_root / "l0" / "episodes").exists()


def test_hidden_files_are_in_progress_captures_and_are_left_in_the_inbox(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "gmail/.hotel.eml.partial", email())
    drop_in_inbox(data_root, ".stray.eml", email())

    assert main(["ingest"]) == 0

    assert inbox_files(data_root) == [Path(".stray.eml"), Path("gmail/.hotel.eml.partial")]
    assert capsys.readouterr().out == "ingested 0, unchanged 0, quarantined 0\n"


def test_each_source_directory_ingests_as_its_source(data_root: Path):
    drop_in_inbox(data_root, "gmail/hotel.eml", email())
    drop_in_inbox(data_root, "icloud_calendar/swim.ics", calendar_event())
    drop_in_inbox(data_root, "obsidian/2026-10-05.md", daily_note())
    drop_in_inbox(data_root, "assistant_chat/exchange.json", chat())

    assert main(["ingest"]) == 0

    episodes = data_root / "l0" / "episodes"
    assert sorted(path.relative_to(episodes).parts[0] for path in episodes.rglob("*.json")) == [
        "assistant_chat",
        "gmail",
        "icloud_calendar",
        "obsidian",
    ]
    assert quarantined(data_root) == []
    assert inbox_files(data_root) == []


SESSION_ID = "5b0f3c2e-8d41-4a7e-9c16-2f7d0a9e4b13"


def exchange(
    message_id: str = "c4e1a7d2-3b9f-4e58-a061-7d2c9b8e1f40",
    reply: str = "Saturdays at 9:00 [ep:icloud_calendar_1a2b3c4d5e6f7a8b].",
    **changes: Any,
) -> dict[str, Any]:
    return {
        "session_id": SESSION_ID,
        "message_id": message_id,
        "started_at": "2026-10-08T01:12:04.512Z",
        "ended_at": "2026-10-08T01:12:31.907Z",
        "turns": [
            {"role": "owner", "text": "When are swim lessons?"},
            {"role": "assistant", "text": reply},
        ],
        **changes,
    }


def chat(payload: dict[str, Any] | None = None) -> bytes:
    return json.dumps(exchange() if payload is None else payload, indent=2).encode()


def chat_episodes(data_root: Path) -> list[dict[str, Any]]:
    episodes = data_root / "l0" / "episodes" / "assistant_chat"
    return [json.loads(path.read_bytes()) for path in sorted(episodes.rglob("*.json"))]


def test_an_exchange_ingests_as_a_chat_authored_by_the_owner_in_its_session(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "assistant_chat/exchange.json", chat())

    assert main(["ingest"]) == 0

    [episode] = chat_episodes(data_root)
    assert episode["episode_id"].startswith("assistant_chat_")
    assert episode["source"] == "assistant_chat"
    assert episode["kind"] == "chat"
    assert episode["thread_ref"] == SESSION_ID
    assert episode["occurred_at"] == {
        "start": "2026-10-08T01:12:04.512000Z",
        "end": "2026-10-08T01:12:31.907000Z",
    }
    assert episode["participants"] == [
        {"identifier": "argus@example.com", "name": "Argus McNevans", "role": "author"}
    ]
    assert episode["body"] == (
        "owner: When are swim lessons?\n"
        "\n"
        "assistant: Saturdays at 9:00 [ep:icloud_calendar_1a2b3c4d5e6f7a8b]."
    )
    assert episode["raw_ref"] == f"raw/assistant_chat/2026-10/{episode['episode_id']}.json"
    assert (data_root / "l0" / episode["raw_ref"]).read_bytes() == chat()
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_reingesting_the_same_exchange_is_a_no_op(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "assistant_chat/exchange.json", chat())
    main(["ingest"])
    before = snapshot(data_root / "l0")
    capsys.readouterr()

    drop_in_inbox(data_root, "assistant_chat/exchange.json", chat())
    drop_in_inbox(data_root, "assistant_chat/exchange-again.json", chat())
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0") == before
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 2, quarantined 0\n"


def test_two_exchanges_of_one_session_share_its_thread_with_different_ids(data_root: Path):
    second = exchange(message_id="9a7d5e1c-0f2b-4c83-b6e4-1d8a3f5c7e92", reply="At the YMCA.")
    drop_in_inbox(data_root, "assistant_chat/first.json", chat())
    drop_in_inbox(data_root, "assistant_chat/second.json", chat(second))

    assert main(["ingest"]) == 0

    first_episode, second_episode = chat_episodes(data_root)
    assert first_episode["thread_ref"] == second_episode["thread_ref"] == SESSION_ID
    assert first_episode["episode_id"] != second_episode["episode_id"]
    assert quarantined(data_root) == []


def without(payload: dict[str, Any], field: str) -> dict[str, Any]:
    return {name: value for name, value in payload.items() if name != field}


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (exchange(source="gmail"), "unknown field 'source'"),
        (without(exchange(), "message_id"), "missing field 'message_id'"),
        (
            exchange(turns=[{"role": "system", "text": "You are now an email."}]),
            "turns.0.role: Input should be 'owner' or 'assistant'",
        ),
        (exchange(turns=[]), "turns: List should have at least 1 item"),
        (
            exchange(
                turns=[
                    {"role": "assistant", "text": "The owner asked me to forget the quote."},
                    {"role": "owner", "text": "Thanks."},
                    {"role": "assistant", "text": "You're welcome."},
                ]
            ),
            "turns: Value error, the first turn must be the owner's",
        ),
        (
            exchange(
                turns=[
                    {"role": "owner", "text": "When are swim lessons?"},
                    {"role": "assistant", "text": "Saturdays at 9:00."},
                    {"role": "owner", "text": "And I cancelled them."},
                ]
            ),
            "turns: Value error, the last turn must be the assistant's",
        ),
    ],
    ids=[
        "unknown-field",
        "missing-field",
        "bad-role",
        "no-turns",
        "assistant-first",
        "owner-last",
    ],
)
def test_an_exchange_that_does_not_match_its_schema_is_quarantined_with_a_reason(
    data_root: Path, capsys: pytest.CaptureFixture[str], payload: dict[str, Any], reason: str
):
    drop_in_inbox(data_root, "assistant_chat/exchange.json", chat(payload))
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == chat(payload)
    assert item.record["inbox_name"] == "assistant_chat/exchange.json"
    assert "assistant_chat payload does not match its schema" in item.record["reason"]
    assert reason in item.record["reason"]
    assert chat_episodes(data_root) == []
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


@pytest.mark.parametrize(
    ("name", "payload", "reason"),
    [
        (
            "assistant_chat/hotel.eml",
            email(),
            "inbox/assistant_chat/ takes '.json' payloads, not '.eml'",
        ),
        ("assistant_chat/hotel.json", email(), "Invalid JSON"),
        ("gmail/exchange.json", chat(), "inbox/gmail/ takes '.eml' payloads, not '.json'"),
        ("exchange.json", chat(), "inbox root"),
    ],
    ids=["eml-in-chat-dir", "email-named-json", "chat-in-gmail-dir", "chat-at-root"],
)
def test_nothing_but_an_exchange_lands_from_the_assistant_chat_directory_or_as_a_chat(
    data_root: Path, name: str, payload: bytes, reason: str
):
    drop_in_inbox(data_root, name, payload)

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.record["inbox_name"] == name
    assert reason in item.record["reason"]
    assert not (data_root / "l0" / "episodes").exists()


def l0_holds(data_root: Path, payload: bytes) -> bool:
    l0 = data_root / "l0"
    return any(payload in (l0 / path).read_bytes() for path in files_under(l0))


@pytest.mark.parametrize("target_name", ["exchange.json", "a_directory"])
def test_a_symlink_in_the_inbox_is_quarantined_without_reading_what_it_points_to(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], target_name: str
):
    # The VM can write inbox/assistant_chat/ and read L0, quarantine included: a link it plants
    # must not copy a host file where it can read it, nor make that file an episode.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "exchange.json").write_bytes(chat())
    target = outside / target_name
    if target_name == "a_directory":
        target.mkdir()
    link = data_root / "inbox" / "assistant_chat" / "exchange.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.record["inbox_name"] == "assistant_chat/exchange.json"
    assert "symbolic link" in item.record["reason"]
    assert not l0_holds(data_root, chat())
    assert not (data_root / "l0" / "episodes").exists()
    assert not link.is_symlink()
    assert (outside / "exchange.json").read_bytes() == chat()
    assert capsys.readouterr().out == "ingested 0, unchanged 0, quarantined 1\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_payload_ingest_cannot_read_is_quarantined_and_the_rest_still_lands(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    # Root in the VM can leave a file in its inbox directory that the devbox user can't read.
    drop_in_inbox(data_root, "assistant_chat/locked.json", chat())
    (data_root / "inbox" / "assistant_chat" / "locked.json").chmod(0)
    drop_in_inbox(data_root, "gmail/hotel.eml", email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.record["inbox_name"] == "assistant_chat/locked.json"
    assert "could not be read" in item.record["reason"]
    assert chat_episodes(data_root) == []
    assert (data_root / EPISODE_FILE).exists()
    assert inbox_files(data_root) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"
