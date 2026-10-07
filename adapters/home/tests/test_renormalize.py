import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pa_core.catalog import CatalogEntry, SqliteCatalog
from pa_core.normalizers import email as email_normalizer
from pa_home.cli import main

EMAIL_ID = "gmail_9ff7394d0df8584d"


def email(body: str = "The hotel block closes on Oct 9.") -> bytes:
    return (
        "X-Gmail-Labels: Inbox\r\n"
        "Message-ID: <wedding-0001@example.net>\r\n"
        "Date: Wed, 30 Sep 2026 21:30:00 -0500\r\n"
        "From: David Kim <dkim@example.net>\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        "Subject: Hotel block\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode()


def calendar_event(sequence: int = 0, start: str = "20261017T090000") -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "X-WR-CALNAME:Family",
        "BEGIN:VEVENT",
        "UID:swim-lessons-0001@icloud.com",
        f"SEQUENCE:{sequence}",
        "DTSTAMP:20261001T140000Z",
        f"DTSTART;TZID=America/Chicago:{start}",
        "SUMMARY:Swim lessons",
        "ORGANIZER;CN=Argus McNevans:mailto:argus@example.com",
        "ATTENDEE:mailto:mara@example.com",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(f"{line}\r\n" for line in lines).encode()


def daily_note(text: str = "## Swim lessons\n\nMara prefers weekday evenings.\n") -> bytes:
    return f"Vault-Path: Daily/2026-10-05.md\n\n{text}".encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    return root


def ingest(data_root: Path, payloads: dict[str, bytes]) -> None:
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (inbox / name).write_bytes(payload)
    assert main(["ingest"]) == 0


def ingest_a_bit_of_everything(data_root: Path) -> None:
    ingest(
        data_root,
        {"hotel.eml": email(), "swim.ics": calendar_event(), "2026-10-05.md": daily_note()},
    )
    ingest(
        data_root,
        {
            "swim.ics": calendar_event(sequence=1, start="20261017T100000"),
            "2026-10-05.md": daily_note("## Swim lessons\n\nSaturdays.\n"),
        },
    )


def envelopes(data_root: Path) -> dict[str, dict[str, Any]]:
    """Every stored envelope, by episode id."""
    files = (data_root / "l0" / "episodes").glob("*/*/*.json")
    stored = (json.loads(path.read_bytes()) for path in files)
    return {envelope["episode_id"]: envelope for envelope in stored}


def files_under(path: Path) -> list[Path]:
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_after_a_normalizer_change_envelopes_reflect_the_new_code_with_the_same_ids(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ingest_a_bit_of_everything(data_root)
    before = envelopes(data_root)
    capsys.readouterr()
    monkeypatch.setattr(email_normalizer, "_body", lambda message: "Rewritten by the new code.")

    assert main(["renormalize"]) == 0

    after = envelopes(data_root)
    assert after.keys() == before.keys()
    assert len(after) == 5
    assert after[EMAIL_ID]["body"] == "Rewritten by the new code."
    assert after[EMAIL_ID]["content_hash"] != before[EMAIL_ID]["content_hash"]
    assert after[EMAIL_ID]["captured_at"] == before[EMAIL_ID]["captured_at"]
    for episode_id in before.keys() - {EMAIL_ID}:
        assert after[episode_id] == before[episode_id]
    episodes = data_root / "l0" / "episodes"
    assert all(is_read_only(episodes / path) for path in files_under(episodes))
    assert capsys.readouterr().out == "renormalized 5 episodes; catalog rebuilt\n"


def snapshot(directory: Path) -> dict[Path, bytes]:
    return {path: (directory / path).read_bytes() for path in files_under(directory)}


def test_raw_payloads_and_quarantine_are_byte_identical_after_renormalize(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    ingest_a_bit_of_everything(data_root)
    ingest(data_root, {"broken.ics": b"not a calendar"})
    raw_before = snapshot(data_root / "l0" / "raw")
    quarantine_before = snapshot(data_root / "l0" / "quarantine")
    monkeypatch.setattr(email_normalizer, "_body", lambda message: "Rewritten by the new code.")

    assert main(["renormalize"]) == 0

    assert snapshot(data_root / "l0" / "raw") == raw_before
    assert snapshot(data_root / "l0" / "quarantine") == quarantine_before
    assert len(quarantine_before) == 2


def test_an_id_mismatch_aborts_and_leaves_the_original_envelopes_and_catalog(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ingest_a_bit_of_everything(data_root)
    l0_before = snapshot(data_root / "l0")
    catalog_before = (data_root / "catalog" / "catalog.sqlite").read_bytes()
    capsys.readouterr()
    monkeypatch.setattr(email_normalizer, "_body", lambda message: "Rewritten by the new code.")
    monkeypatch.setattr(
        email_normalizer, "episode_id", lambda source, native_id: "gmail_0000000000000000"
    )

    assert main(["renormalize"]) == 1

    assert snapshot(data_root / "l0") == l0_before
    assert (data_root / "catalog" / "catalog.sqlite").read_bytes() == catalog_before
    assert sorted(p.name for p in (data_root / "l0").iterdir()) == ["episodes", "raw"]
    error = capsys.readouterr().err
    assert "renormalize aborted" in error
    assert EMAIL_ID in error
    assert "gmail_0000000000000000" in error


def test_a_payload_that_no_longer_normalizes_aborts_and_leaves_the_original_envelopes(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ingest_a_bit_of_everything(data_root)
    l0_before = snapshot(data_root / "l0")
    capsys.readouterr()

    def broken_body(message: object) -> str:
        raise ValueError("the new code cannot read this body")

    monkeypatch.setattr(email_normalizer, "_body", broken_body)

    assert main(["renormalize"]) == 1

    assert snapshot(data_root / "l0") == l0_before
    error = capsys.readouterr().err
    assert EMAIL_ID in error
    assert "the new code cannot read this body" in error


def catalog_entries(data_root: Path) -> list[CatalogEntry]:
    with SqliteCatalog(data_root / "catalog" / "catalog.sqlite") as catalog:
        return catalog.entries()


def test_the_catalog_matches_the_new_envelopes_afterwards(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    ingest_a_bit_of_everything(data_root)
    monkeypatch.setattr(email_normalizer, "_body", lambda message: "Rewritten by the new code.")

    assert main(["renormalize"]) == 0

    stored = envelopes(data_root)
    entries = catalog_entries(data_root)
    assert {entry.episode_id: entry.content_hash for entry in entries} == {
        episode_id: envelope["content_hash"] for episode_id, envelope in stored.items()
    }
    (data_root / "catalog" / "catalog.sqlite").unlink()
    assert main(["catalog", "rebuild"]) == 0
    assert catalog_entries(data_root) == entries


def test_an_envelope_whose_time_moves_to_another_month_still_points_at_its_raw_payload(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    ingest(data_root, {"hotel.eml": email()})
    original_date = email_normalizer._date
    # Say a fix reads the Date header as UTC: 30 Sep 21:30 stays in September.
    monkeypatch.setattr(
        email_normalizer, "_date", lambda value: original_date(value).replace(tzinfo=UTC)
    )

    assert main(["renormalize"]) == 0

    assert files_under(data_root / "l0") == [
        Path("episodes/gmail/2026-09") / f"{EMAIL_ID}.json",
        Path("raw/gmail/2026-10") / f"{EMAIL_ID}.eml",
    ]
    [entry] = catalog_entries(data_root)
    assert entry.occurred_at.start == datetime(2026, 9, 30, 21, 30, tzinfo=UTC)
    assert entry.episode_ref == f"episodes/gmail/2026-09/{EMAIL_ID}.json"
    assert (data_root / "l0" / entry.raw_ref).read_bytes() == email()


def test_after_the_owner_changes_name_renormalize_lets_a_recaptured_note_ingest_unchanged(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ingest(data_root, {"2026-10-05.md": daily_note()})
    [before] = envelopes(data_root).values()
    monkeypatch.setenv("PA_OWNER_NAME", "Gus McNevans")

    assert main(["renormalize"]) == 0

    [after] = envelopes(data_root).values()
    assert after["episode_id"] == before["episode_id"]
    assert after["participants"] == [
        {"identifier": "argus@example.com", "name": "Gus McNevans", "role": "author"}
    ]
    capsys.readouterr()
    ingest(data_root, {"2026-10-05.md": daily_note()})
    assert capsys.readouterr().out == "ingested 0, unchanged 1, quarantined 0\n"


def test_renormalize_refuses_to_start_without_the_owner_configured(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    ingest(data_root, {"2026-10-05.md": daily_note()})
    l0_before = snapshot(data_root / "l0")
    monkeypatch.delenv("PA_OWNER_NAME")

    assert main(["renormalize"]) == 1

    assert "Owner configuration is missing" in capsys.readouterr().err
    assert snapshot(data_root / "l0") == l0_before


def test_a_tree_left_by_an_interrupted_renormalize_is_discarded_by_the_next_one(data_root: Path):
    ingest(data_root, {"hotel.eml": email()})
    stale = data_root / "l0" / ".episodes.renormalize" / "gmail" / "2026-10" / "gmail_stale.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")

    assert main(["renormalize"]) == 0

    assert files_under(data_root / "l0") == [
        Path("episodes/gmail/2026-10") / f"{EMAIL_ID}.json",
        Path("raw/gmail/2026-10") / f"{EMAIL_ID}.eml",
    ]


def test_renormalizing_an_empty_l0_succeeds_with_nothing_to_do(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["renormalize"]) == 0

    assert capsys.readouterr().out == "renormalized 0 episodes; catalog rebuilt\n"
    assert catalog_entries(data_root) == []
