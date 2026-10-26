"""`pa inspect`: the structure of a payload, an envelope, or a quarantine record, never its content.

Every fixture here is salted with distinctive strings standing in for real names, addresses,
subjects, bodies, locations, URLs, user labels, and timestamps. Whatever the payload, none of
them may appear in what `pa inspect` prints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.catalog import SqliteCatalog
from pa_core.ingest import ingest
from pa_core.owner import Owner
from pa_home.cli import main
from pa_home.filesystem_l0 import FilesystemL0

# What a real payload would hold, none of which `pa inspect` may print.
NAME = "Zebulon Quackenbush"
ADDRESS = "zq.hush@quackmail.example"
OTHER_ADDRESS = "periwinkle.otter@burrow.example"
SUBJECT = "Clandestine Pumpkin Summit"
BODY = "The marmalade vault opens at the third gong."
HTML_BODY = "Wear the tangerine galoshes."
LOCATION = "Snorkel Hall, 42 Wobble Lane"
URL = "https://hush.example/zq-meet"
USER_LABEL = "Project Moonwhistle"
DESCRIPTION = "Bring the periwinkle ledger."
ATTACHMENT_NAME = "secret-turnip-plans.pdf"
SALT = (
    NAME,
    "Zebulon",
    "Quackenbush",
    ADDRESS,
    "quackmail",
    OTHER_ADDRESS,
    "burrow",
    SUBJECT,
    "Pumpkin",
    BODY,
    "marmalade",
    HTML_BODY,
    "tangerine",
    LOCATION,
    "Snorkel",
    "Wobble",
    URL,
    "hush.example",
    USER_LABEL,
    "Moonwhistle",
    DESCRIPTION,
    "ledger",
    ATTACHMENT_NAME,
    "turnip",
    "2026",
)


@pytest.fixture(autouse=True)
def no_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pa inspect` reads only the path it's given: no data root or owner is configured."""
    for name in ("PA_DATA_DIR", "PA_OWNER_NAME", "PA_OWNER_EMAILS", "PA_OWNER_OTHER_NAMES"):
        monkeypatch.delenv(name, raising=False)


def inspect(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """What `pa inspect` prints for `path`, once it's shown to hold none of the salt."""
    assert main(["inspect", str(path)]) == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    leaked = [salt for salt in SALT if salt.lower() in output.lower()]
    assert leaked == [], output
    return captured.out


def write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def email(labels: str = f"Inbox,Category Promotions,{USER_LABEL},Starred") -> bytes:
    return (
        f"X-Gmail-Labels: {labels}\r\n"
        "Message-ID: <zq-0001@quackmail.example>\r\n"
        "Date: Sat, 17 Oct 2026 09:00:00 -0500\r\n"
        f"From: {NAME} <{ADDRESS}>\r\n"
        f"To: Otter <{OTHER_ADDRESS}>, {ADDRESS}\r\n"
        f"Subject: {SUBJECT}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="outer"\r\n'
        "\r\n"
        "--outer\r\n"
        'Content-Type: multipart/alternative; boundary="inner"\r\n'
        "\r\n"
        "--inner\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{BODY} See {URL}\r\n"
        "--inner\r\n"
        'Content-Type: text/html; charset="utf-8"\r\n'
        "\r\n"
        f'<p>{HTML_BODY} <a href="{URL}">here</a></p>\r\n'
        "--inner--\r\n"
        "--outer\r\n"
        f'Content-Type: application/pdf; name="{ATTACHMENT_NAME}"\r\n'
        f'Content-Disposition: attachment; filename="{ATTACHMENT_NAME}"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQKJcfsj6IKMSAwIG9iago=\r\n"
        "--outer--\r\n"
    ).encode()


def test_an_email_shows_its_header_names_mime_parts_and_system_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "mail.eml", email()), capsys)

    assert "email" in out
    for header in ("X-Gmail-Labels", "Message-ID", "Date", "From", "To", "Subject"):
        assert header in out
    for content_type in (
        "multipart/mixed",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "application/pdf",
    ):
        assert content_type in out
    assert "attachment" in out
    assert "Inbox" in out
    assert "Category Promotions" in out
    assert "Starred" in out
    assert "1 user label" in out


def series(*, header: bool = True) -> bytes:
    """A recurring series with one changed occurrence, as CalDAV serves it (ADR-0008)."""
    capture = (
        "Calendar: Moonwhistle Family\nCaptured-At: 2026-10-17T09:00:00-05:00\n\n" if header else ""
    )
    return (
        capture + "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Apple Inc.//iCloud 2.0//EN\r\n"
        "BEGIN:VTIMEZONE\r\n"
        "TZID:America/Chicago\r\n"
        "BEGIN:STANDARD\r\n"
        "DTSTART:20261101T020000\r\n"
        "TZOFFSETFROM:-0500\r\n"
        "TZOFFSETTO:-0600\r\n"
        "END:STANDARD\r\n"
        "END:VTIMEZONE\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:zq-series@quackmail.example\r\n"
        "SEQUENCE:2\r\n"
        "DTSTAMP:20261001T140000Z\r\n"
        "LAST-MODIFIED:20261001T135900Z\r\n"
        "DTSTART;TZID=America/Chicago:20261017T090000\r\n"
        "DTEND;TZID=America/Chicago:20261017T094500\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=SA\r\n"
        "EXDATE;TZID=America/Chicago:20261024T090000\r\n"
        f"SUMMARY:{SUBJECT}\r\n"
        f"LOCATION:{LOCATION.replace(',', chr(92) + ',')}\r\n"
        f"DESCRIPTION:{DESCRIPTION}\r\n"
        f"URL:{URL}\r\n"
        f"ORGANIZER;CN={NAME}:mailto:{ADDRESS}\r\n"
        f"ATTENDEE;CN=Otter;PARTSTAT=ACCEPTED:mailto:{OTHER_ADDRESS}\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        "TRIGGER:-PT15M\r\n"
        f"DESCRIPTION:{DESCRIPTION}\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:zq-series@quackmail.example\r\n"
        "RECURRENCE-ID;TZID=America/Chicago:20261031T090000\r\n"
        "SEQUENCE:2\r\n"
        "DTSTAMP:20261001T140000Z\r\n"
        "DTSTART;TZID=America/Chicago:20261031T100000\r\n"
        "DTEND;TZID=America/Chicago:20261031T104500\r\n"
        f"SUMMARY:{SUBJECT}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode()


def single_event() -> bytes:
    """The series' master alone, in today's export shape: no capture header, one event."""
    resource = series(header=False)
    override = resource.index(b"BEGIN:VEVENT", resource.index(b"END:VEVENT"))
    return resource[:override] + b"END:VCALENDAR\r\n"


def test_a_calendar_resource_behind_a_capture_header_shows_its_components_and_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "resource.ics", series()), capsys)

    assert "calendar" in out
    assert "capture header: Calendar, Captured-At" in out
    for component in ("VCALENDAR", "VTIMEZONE", "STANDARD", "VEVENT", "VALARM"):
        assert component in out
    for prop in ("UID", "DTSTART", "SUMMARY", "LOCATION", "DESCRIPTION", "URL", "ATTENDEE"):
        assert prop in out
    assert "events: 2" in out
    assert "RECURRENCE-ID: 1 of 2" in out
    assert "RRULE: 1 of 2" in out
    assert "EXDATE: 1 of 2" in out
    assert "SEQUENCE: 2 of 2" in out
    assert "LAST-MODIFIED: 1 of 2" in out


def test_a_calendar_export_without_a_capture_header_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "event.ics", series(header=False)), capsys)

    assert "capture header: none" in out
    assert "events: 2" in out


def test_a_blank_line_inside_a_calendar_export_is_not_read_as_a_capture_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    spaced = series(header=False).replace(b"END:VTIMEZONE\r\n", b"END:VTIMEZONE\r\n\r\n")

    out = inspect(write(tmp_path, "event.ics", spaced), capsys)

    assert "capture header: none" in out
    assert "VTIMEZONE: TZID" in out
    assert "parse: complete" in out


def daily_note() -> bytes:
    return (
        "Vault-Path: Daily/2026-10-17 Moonwhistle.md\n"
        "Captured-At: 2026-10-18T01:00:00-05:00\n"
        "\n"
        f"## {SUBJECT}\n"
        "\n"
        f"{NAME} says {BODY}\n"
        "\n"
        f"### {LOCATION}\n"
        "\n"
        f"- {URL}\n"
        "\n"
        f"## {DESCRIPTION}\n"
    ).encode()


def test_a_daily_note_shows_its_capture_header_heading_count_and_length(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "note.md", daily_note()), capsys)

    assert "note" in out
    assert "capture header: Vault-Path, Captured-At" in out
    assert "headings: 3" in out
    assert "lines: 9" in out


def exchange() -> bytes:
    return json.dumps(
        {
            "session_id": "zq-session",
            "message_id": "zq-message",
            "started_at": "2026-10-17T09:00:00-05:00",
            "ended_at": "2026-10-17T09:01:00-05:00",
            "turns": [
                {"role": "owner", "text": f"Where is {NAME} meeting? {SUBJECT}"},
                {"role": "assistant", "text": f"At {LOCATION}; see {URL}"},
            ],
        }
    ).encode()


def test_a_chat_exchange_shows_its_fields_and_turns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "exchange.json", exchange()), capsys)

    assert "assistant chat exchange" in out
    assert "fields: session_id, message_id, started_at, ended_at, turns" in out
    assert "turns: 2 (owner 1, assistant 1)" in out


CAPTURED_AT = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def land(tmp_path: Path, inbox_name: str, payload: bytes) -> Path:
    """Ingest one payload into a fresh L0 and return that L0."""
    inbox, l0 = tmp_path / "inbox", tmp_path / "l0"
    (inbox / inbox_name).parent.mkdir(parents=True)
    (inbox / inbox_name).write_bytes(payload)
    owner = Owner(name=NAME, email_addresses=(ADDRESS,))
    with SqliteCatalog(tmp_path / "catalog.sqlite") as catalog:
        ingest(inbox, FilesystemL0(l0), catalog, owner, now=lambda: CAPTURED_AT)
    return l0


def test_an_envelope_shows_which_fields_are_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    l0 = land(tmp_path, "gmail/mail.eml", email())
    [envelope] = (l0 / "episodes").rglob("*.json")

    out = inspect(envelope, capsys)

    assert "envelope" in out
    for name in ("episode_id", "participants", "subject", "body", "labels", "content_hash"):
        assert name in out
    assert "source: gmail" in out
    assert "kind: email" in out
    assert "participants: 3 (sender 1, recipient 2)" in out


def test_a_payload_that_fails_to_parse_reports_how_far_parsing_got(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    resource = series()
    truncated = resource[: resource.index(b"BEGIN:VALARM")]
    unfolded = resource.replace(b"END:VALARM", f"{BODY}\r\nEND:VALARM".encode())

    truncated_out = inspect(write(tmp_path, "truncated.ics", truncated), capsys)
    unfolded_out = inspect(write(tmp_path, "unfolded.ics", unfolded), capsys)
    chat_out = inspect(write(tmp_path, "cut.json", exchange()[:80]), capsys)

    assert "VTIMEZONE: TZID" in truncated_out
    assert "events: 1" in truncated_out
    assert "parse: ended inside VCALENDAR, VEVENT" in truncated_out
    assert "VALARM: ACTION, TRIGGER, DESCRIPTION" in unfolded_out
    assert "parse: stopped at content line 31 of 43" in unfolded_out
    assert "parse: stopped at line 1 column 72 (Unterminated string" in chat_out


def test_text_read_as_an_email_counts_the_header_names_it_does_not_know(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    out = inspect(write(tmp_path, "notes.eml", f"Zebulon: {BODY}\n".encode()), capsys)

    assert "headers: 1 other name" in out


def test_a_file_of_unknown_format_can_be_read_as_a_named_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = write(tmp_path, "resource.txt", series())

    unnamed = inspect(path, capsys)
    assert main(["inspect", "--as", "calendar", str(path)]) == 0
    named = capsys.readouterr().out

    assert "format: unknown" in unnamed
    assert "events: 2" in named


def test_several_files_are_numbered_and_never_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    paths = [
        write(tmp_path, f"{NAME}.eml", email()),
        tmp_path / f"{SUBJECT}.ics",
        write(tmp_path, f"{USER_LABEL}.md", daily_note()),
    ]

    assert main(["inspect", *map(str, paths)]) == 1

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert [salt for salt in SALT if salt.lower() in output.lower()] == []
    assert "file 1 of 3" in captured.out
    assert "file 3 of 3" in captured.out
    assert "file 2 can't be read" in captured.err


# Malformed payloads, each broken where its content is, so a reason that quoted the broken
# value would leak it.
MALFORMED = {
    "email-date": (
        "gmail/mail.eml",
        email().replace(b"Sat, 17 Oct 2026 09:00:00 -0500", f"when {NAME} arrives".encode()),
    ),
    "note-capture-time": (
        "obsidian/note.md",
        daily_note().replace(b"2026-10-18T01:00:00-05:00", f"after {SUBJECT}".encode()),
    ),
    "note-undated-path": (
        "obsidian/note.md",
        daily_note().replace(b"2026-10-17 Moonwhistle", NAME.encode()),
    ),
    "calendar-unfolded-line": (
        "icloud_calendar/event.ics",
        single_event().replace(b"END:VALARM", f"{BODY}\r\nEND:VALARM".encode()),
    ),
    "calendar-unreadable-start": (
        "icloud_calendar/event.ics",
        single_event().replace(b"20261017T090000", NAME.encode()),
    ),
    "calendar-series": ("icloud_calendar/resource.ics", series()),
    "chat-schema": (
        "assistant_chat/exchange.json",
        exchange().replace(b'"owner"', f'"{NAME}"'.encode()),
    ),
}


@pytest.mark.parametrize("case", MALFORMED)
def test_a_quarantined_payload_and_its_record_are_inspected_without_their_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: str
):
    inbox_name, payload = MALFORMED[case]
    l0 = land(tmp_path, inbox_name, payload)
    # The record is the raw payload's path plus `.json`.
    raw, record = sorted((l0 / "quarantine").iterdir(), key=lambda path: len(path.name))

    record_out = inspect(record, capsys)
    raw_out = inspect(raw, capsys)

    assert "quarantine record" in record_out
    assert f"inbox directory: {inbox_name.split('/')[0]}/" in record_out
    assert "reason: " in record_out
    assert "size: " in raw_out
