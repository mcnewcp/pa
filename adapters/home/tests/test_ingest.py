import json
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pa_core.ingest import ingest
from pa_home.cli import main
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
    return root


def drop_in_inbox(data_root: Path, name: str, payload: bytes) -> None:
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_bytes(payload)


def files_under(path: Path) -> list[Path]:
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def snapshot(directory: Path) -> dict[Path, bytes]:
    return {path: (directory / path).read_bytes() for path in files_under(directory)}


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


MALFORMED = Path(__file__).parent / "fixtures" / "malformed"


def drop_fixture_in_inbox(data_root: Path, name: str) -> bytes:
    payload = (MALFORMED / name).read_bytes()
    drop_in_inbox(data_root, name, payload)
    return payload


@dataclass
class Quarantined:
    raw: bytes
    record: dict[str, Any]


def quarantined(data_root: Path) -> list[Quarantined]:
    """Each quarantined item: its raw bytes and its record, which sits beside it as `.json`."""
    quarantine = data_root / "l0" / "quarantine"
    if not quarantine.exists():
        return []
    return [
        Quarantined(
            raw=record_file.with_suffix("").read_bytes(),
            record=json.loads(record_file.read_bytes()),
        )
        for record_file in sorted(quarantine.glob("*.json"))
    ]


def test_ingested_email_lands_in_l0_as_a_read_only_envelope_and_raw_payload(data_root: Path):
    drop_in_inbox(data_root, "hotel.eml", email())

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
    drop_in_inbox(data_root, "hotel.eml", email())

    main(["ingest"])

    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_reingesting_the_same_email_adds_nothing_and_reports_it_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "hotel.eml", email())
    main(["ingest"])
    before = snapshot(data_root / "l0")
    capsys.readouterr()

    drop_in_inbox(data_root, "hotel-again.eml", email())
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0") == before
    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"


def test_a_recapture_that_only_changes_labels_is_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "hotel.eml", email(labels="Inbox"))
    main(["ingest"])
    capsys.readouterr()

    drop_in_inbox(data_root, "hotel.eml", email(labels="Archived,Wedding"))
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
    drop_in_inbox(data_root, "hotel.eml", email())
    drop_in_inbox(data_root, "shuttle.eml", email(message_id="wedding-0002@example.net"))

    assert main(["ingest"]) == 0

    assert leftover.read_bytes() == email(body="Closes on Oct 2.")
    assert not (data_root / EPISODE_FILE).exists()
    [item] = quarantined(data_root)
    assert item.raw == email()
    assert item.record["inbox_name"] == "hotel.eml"
    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


def test_a_labels_only_recapture_finishes_an_interrupted_ingest_from_its_leftover_raw(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    leftover = leave_raw_from_an_interrupted_ingest(data_root, email(labels="Inbox"))
    drop_in_inbox(data_root, "hotel.eml", email(labels="Archived"))

    assert main(["ingest"]) == 0

    assert leftover.read_bytes() == email(labels="Inbox")
    episode = json.loads((data_root / EPISODE_FILE).read_bytes())
    assert episode["labels"] == ["Inbox"]
    assert quarantined(data_root) == []
    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 0\n"


def test_an_interrupted_ingest_completes_when_the_payload_is_recaptured(data_root: Path):
    leave_raw_from_an_interrupted_ingest(data_root, email())
    drop_in_inbox(data_root, "hotel.eml", email())

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
        ("unknown_shape.txt", "no normalizer for '.txt'"),
        ("unknown_charset.eml", "unknown encoding: x-nonexistent"),
        ("undecodable_sender.eml", "codec can't encode"),
    ],
)
def test_a_malformed_payload_is_quarantined_with_a_reason_and_the_rest_still_ingests(
    data_root: Path, capsys: pytest.CaptureFixture[str], fixture: str, reason: str
):
    broken = drop_fixture_in_inbox(data_root, fixture)
    drop_in_inbox(data_root, "hotel.eml", email())

    assert main(["ingest"]) == 0

    [item] = quarantined(data_root)
    assert item.raw == broken
    assert item.record["inbox_name"] == fixture
    assert reason in item.record["reason"]
    assert datetime.fromisoformat(item.record["quarantined_at"]).tzinfo is not None
    assert (data_root / EPISODE_FILE).exists()
    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 1, unchanged 0, quarantined 1\n"


def test_a_recapture_with_different_content_is_quarantined_and_the_episode_is_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "hotel.eml", email(body="The hotel block closes on Oct 9."))
    main(["ingest"])
    episodes_before = snapshot(data_root / "l0" / "episodes")
    raw_before = snapshot(data_root / "l0" / "raw")
    capsys.readouterr()

    edited = email(body="The hotel block closes on Oct 2.")
    drop_in_inbox(data_root, "hotel.eml", edited)
    assert main(["ingest"]) == 0

    assert snapshot(data_root / "l0" / "episodes") == episodes_before
    assert snapshot(data_root / "l0" / "raw") == raw_before
    [item] = quarantined(data_root)
    assert item.raw == edited
    assert item.record["inbox_name"] == "hotel.eml"
    assert EPISODE_ID in item.record["reason"]
    assert list((data_root / "inbox").iterdir()) == []
    assert capsys.readouterr().out == "ingested 0, unchanged 0, quarantined 1\n"


def test_a_payload_stays_in_the_inbox_when_it_cannot_be_written_to_quarantine(data_root: Path):
    broken = drop_fixture_in_inbox(data_root, "no_message_id.eml")
    # A file where the quarantine directory belongs makes every quarantine write fail.
    (data_root / "l0").mkdir()
    (data_root / "l0" / "quarantine").write_bytes(b"")

    with pytest.raises(OSError):
        main(["ingest"])

    assert (data_root / "inbox" / "no_message_id.eml").read_bytes() == broken


def ingest_at(data_root: Path, moment: datetime):
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return ingest(inbox, FilesystemL0(data_root / "l0"), now=lambda: moment)


def test_a_recapture_that_only_changes_capture_time_is_unchanged(data_root: Path):
    drop_in_inbox(data_root, "hotel.eml", email())
    ingest_at(data_root, datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    before = snapshot(data_root / "l0")

    drop_in_inbox(data_root, "hotel.eml", email())
    summary = ingest_at(data_root, datetime(2026, 10, 5, 8, 0, tzinfo=UTC))

    assert (summary.ingested, summary.unchanged, summary.quarantined) == (0, 1, 0)
    assert snapshot(data_root / "l0") == before


def test_the_same_payload_quarantined_twice_in_one_second_keeps_both_and_the_rest_ingests(
    data_root: Path,
):
    broken = drop_fixture_in_inbox(data_root, "no_message_id.eml")
    ingest_at(data_root, datetime(2026, 10, 1, 8, 0, 0, 100_000, tzinfo=UTC))

    drop_fixture_in_inbox(data_root, "no_message_id.eml")
    drop_in_inbox(data_root, "hotel.eml", email())
    summary = ingest_at(data_root, datetime(2026, 10, 1, 8, 0, 0, 200_000, tzinfo=UTC))

    assert (summary.ingested, summary.unchanged, summary.quarantined) == (1, 0, 1)
    assert [item.raw for item in quarantined(data_root)] == [broken, broken]
    assert list((data_root / "inbox").iterdir()) == []
