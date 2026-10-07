from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.envelope import Participant, Role
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers.email import normalize_email

FIXTURES = Path(__file__).parent / "fixtures" / "email"
CAPTURED_AT = datetime(2026, 10, 2, 7, 0, tzinfo=UTC)


def normalize_fixture(name: str):
    return normalize_email((FIXTURES / name).read_bytes(), captured_at=CAPTURED_AT)


def test_participants_come_from_from_to_and_cc_headers():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.participants == [
        Participant(identifier="dkim@example.net", name="David Kim", role=Role.SENDER),
        Participant(identifier="argus@example.com", name="Argus McNevans", role=Role.RECIPIENT),
        Participant(identifier="mara@example.com", name="Mara McNevans", role=Role.RECIPIENT),
        Participant(identifier="wedding-party@example.org", name=None, role=Role.CC),
    ]


def test_episode_id_is_derived_from_the_internet_message_id():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.native_id == "CAF3x9-reply-0002@mail.example.net"
    assert envelope.episode_id == "gmail_94f6b4cbc55a3b85"


def test_occurred_at_keeps_the_original_timezone_offset():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-09-30T21:30:00-05:00",
        "end": None,
    }


def test_subject_and_plain_text_body_are_decoded():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.subject == "Re: Hotel block for the wedding"
    assert envelope.body == "Hi both,\n\nThe hotel block closes on Oct 9 — book soon!\n\nDave"


def test_thread_is_the_root_of_the_references_chain():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.thread_ref == "CAF3x9-root-0000@mail.example.com"


def test_thread_falls_back_to_in_reply_to():
    envelope = normalize_fixture("in_reply_to_only.eml")

    assert envelope.thread_ref == "CAF3x9-root-0000@mail.example.com"


def test_a_message_without_threading_headers_starts_its_own_thread():
    envelope = normalize_fixture("thread_start.eml")

    assert envelope.thread_ref == "CAF3x9-root-0000@mail.example.com"


def test_gmail_labels_are_recorded():
    envelope = normalize_fixture("reply_with_cc.eml")

    assert envelope.labels == ["Inbox", "Important", "Category Personal"]


def test_recapture_with_new_labels_and_capture_time_keeps_the_content_hash():
    raw = (FIXTURES / "reply_with_cc.eml").read_bytes()
    relabelled = raw.replace(b"Inbox,Important,Category Personal", b"Archived,Wedding")

    first = normalize_email(raw, captured_at=CAPTURED_AT)
    second = normalize_email(relabelled, captured_at=datetime(2026, 11, 1, tzinfo=UTC))

    assert second.labels != first.labels
    assert second.content_hash == first.content_hash


def test_a_changed_body_changes_the_content_hash():
    raw = (FIXTURES / "reply_with_cc.eml").read_bytes()
    edited = raw.replace(b"book soon!", b"book today!")

    first = normalize_email(raw, captured_at=CAPTURED_AT)
    second = normalize_email(edited, captured_at=CAPTURED_AT)

    assert second.episode_id == first.episode_id
    assert second.content_hash != first.content_hash


@pytest.mark.parametrize("header", [b"Message-ID", b"Date"])
def test_a_message_missing_its_id_or_date_is_malformed(header: bytes):
    raw = (FIXTURES / "thread_start.eml").read_bytes()
    without_header = b"\n".join(
        line for line in raw.split(b"\n") if not line.startswith(header + b":")
    )

    with pytest.raises(MalformedPayloadError, match=header.decode()):
        normalize_email(without_header, captured_at=CAPTURED_AT)


def test_an_html_only_message_has_its_text_as_the_body():
    envelope = normalize_fixture("html_only.eml")

    assert envelope.body == "Your flight is confirmed.\nConfirmation: QX7P2L\nDeparts Fri 6:40 PM"
