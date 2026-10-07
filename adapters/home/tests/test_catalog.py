import json
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
