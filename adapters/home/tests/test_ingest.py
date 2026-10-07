import stat
from pathlib import Path

import pytest

from pa_home.cli import main

# Sent late on 30 Sep in Chicago, which is already October in UTC.
EPISODE_ID = "gmail_9ff7394d0df8584d"
EPISODE_FILE = Path("l0/episodes/gmail/2026-10") / f"{EPISODE_ID}.json"
RAW_FILE = Path("l0/raw/gmail/2026-10") / f"{EPISODE_ID}.eml"


def email(labels: str = "Inbox", body: str = "The hotel block closes on Oct 9.") -> bytes:
    return (
        f"X-Gmail-Labels: {labels}\r\n"
        "Message-ID: <wedding-0001@example.net>\r\n"
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
    assert capsys.readouterr().out == "ingested 1, unchanged 0\n"


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
    assert capsys.readouterr().out == "ingested 0, unchanged 1\n"


def test_a_recapture_that_only_changes_labels_is_unchanged(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    drop_in_inbox(data_root, "hotel.eml", email(labels="Inbox"))
    main(["ingest"])
    capsys.readouterr()

    drop_in_inbox(data_root, "hotel.eml", email(labels="Archived,Wedding"))
    main(["ingest"])

    assert capsys.readouterr().out == "ingested 0, unchanged 1\n"
    assert (data_root / RAW_FILE).read_bytes() == email(labels="Inbox")


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


def test_a_recapture_with_different_content_never_overwrites_the_episode(data_root: Path):
    drop_in_inbox(data_root, "hotel.eml", email(body="The hotel block closes on Oct 9."))
    main(["ingest"])
    before = snapshot(data_root / "l0")

    drop_in_inbox(data_root, "hotel.eml", email(body="The hotel block closes on Oct 2."))
    main(["ingest"])

    assert snapshot(data_root / "l0") == before


def test_a_raw_payload_left_by_an_interrupted_ingest_is_never_replaced(data_root: Path):
    leftover = data_root / RAW_FILE
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(email(labels="Inbox"))
    leftover.chmod(0o444)
    drop_in_inbox(data_root, "hotel.eml", email(labels="Archived"))

    main(["ingest"])

    assert leftover.read_bytes() == email(labels="Inbox")


def test_an_interrupted_ingest_completes_when_the_payload_is_recaptured(data_root: Path):
    leftover = data_root / RAW_FILE
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(email())
    leftover.chmod(0o444)
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
