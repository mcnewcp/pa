from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.envelope import Kind, Participant, Role, Source
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers.daily_note import normalize_daily_note
from pa_core.owner import Owner

FIXTURES = Path(__file__).parent / "fixtures" / "daily_notes"
CAPTURED_AT = datetime(2026, 10, 6, 4, 0, tzinfo=UTC)
NOTE = (FIXTURES / "daily_note.md").read_bytes()
OWNER = Owner(
    name="Argus McNevans",
    email_addresses=("argus@example.com", "argus.mcnevans@example.org"),
    other_names=("Gus",),
)


def normalize(raw: bytes = NOTE):
    return normalize_daily_note(raw, captured_at=CAPTURED_AT, owner=OWNER)


def test_a_daily_note_is_a_note_from_obsidian_authored_by_the_owner():
    envelope = normalize()

    assert envelope.source == Source.OBSIDIAN
    assert envelope.kind == Kind.NOTE
    assert envelope.participants == [
        Participant(identifier="argus@example.com", name="Argus McNevans", role=Role.AUTHOR)
    ]


def test_occurred_at_is_the_notes_day():
    envelope = normalize()

    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-10-05T00:00:00Z",
        "end": "2026-10-06T00:00:00Z",
    }


def test_the_body_is_the_note_with_its_topic_headings():
    envelope = normalize()

    assert envelope.body == (
        "## Swim lessons\n"
        "\n"
        "Mara thinks weekday evenings work best, since her late shifts are mostly weekends.\n"
        "Registration closes Friday. I'll sign Theo and June up by Thursday.\n"
        "\n"
        "## Kitchen\n"
        "\n"
        "Rob from Brightline said the revised quote is coming this week."
    )


def test_the_vault_path_is_the_native_id_and_thread_and_with_the_note_text_makes_the_id():
    envelope = normalize()

    assert envelope.native_id == "Daily/2026-10-05.md"
    assert envelope.thread_ref == "Daily/2026-10-05.md"
    assert envelope.episode_id == "obsidian_0510599eb323eee0"
    assert envelope.raw_ref == "raw/obsidian/2026-10/obsidian_0510599eb323eee0.md"


CORRECTED = NOTE.replace(b"weekday evenings work best", b"Saturday mornings work best")


def test_a_corrected_note_is_a_new_version_in_the_same_thread():
    original = normalize()
    correction = normalize(CORRECTED)

    assert correction.episode_id != original.episode_id
    assert correction.thread_ref == original.thread_ref
    assert "Saturday mornings work best" in correction.body


def test_a_recapture_of_the_same_note_is_the_same_episode_with_the_same_content():
    first = normalize()
    second = normalize_daily_note(NOTE, captured_at=datetime(2026, 10, 9, tzinfo=UTC), owner=OWNER)

    assert second.episode_id == first.episode_id
    assert second.content_hash == first.content_hash


def test_a_note_with_windows_line_endings_has_the_same_body():
    crlf = NOTE.replace(b"\n", b"\r\n")

    assert normalize(crlf).body == normalize().body


NOTE_TEXT = NOTE[NOTE.index(b"## Swim") :]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (NOTE_TEXT, "no capture header"),
        (b"Vault-Path: Daily/2026-10-05.md\n", "no capture header"),
        (NOTE.replace(b"Vault-Path:", b"Captured-By:"), "no Vault-Path"),
        (NOTE.replace(b"Daily/2026-10-05.md", b""), "no Vault-Path"),
        (NOTE.replace(b"2026-10-05", b"Swim plans"), "no date in its name"),
        (NOTE.replace(b"2026-10-05", b"2026-13-05"), "no date in its name"),
        (NOTE.replace(b"Theo", "Th\xe9o".encode("latin-1")), "not UTF-8"),
    ],
    ids=["no-header", "header-only", "no-path", "empty-path", "undated", "bad-date", "not-utf8"],
)
def test_a_note_without_a_readable_capture_header_or_dated_path_is_malformed(
    raw: bytes, reason: str
):
    with pytest.raises(MalformedPayloadError, match=reason):
        normalize(raw)


def test_an_owner_needs_an_email_address_to_author_notes():
    with pytest.raises(ValueError, match="at least one email address"):
        Owner(name="Argus McNevans", email_addresses=())
