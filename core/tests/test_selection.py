from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.envelope import Envelope
from pa_core.normalizers.daily_note import normalize_daily_note
from pa_core.normalizers.email import normalize_email
from pa_core.owner import Owner
from pa_core.selection import CHECKED, SKIPPED, Category, category, latest_note_versions

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURED_AT = datetime(2026, 10, 8, tzinfo=UTC)
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
NOTE = (FIXTURES / "daily_notes" / "daily_note.md").read_bytes()


def mail(labels: list[str]) -> Envelope:
    raw = (FIXTURES / "email" / "thread_start.eml").read_bytes()
    return normalize_email(raw, captured_at=CAPTURED_AT).model_copy(update={"labels": labels})


def note(captured: str, text: bytes = b"weekday evenings work best") -> Envelope:
    raw = NOTE.replace(b"2026-10-06T01:00:00-05:00", captured.encode()).replace(
        b"weekday evenings work best", text
    )
    return normalize_daily_note(raw, captured_at=CAPTURED_AT, owner=OWNER)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["Inbox", "Category Promotions"], Category.PROMOTIONS),
        (["Inbox", "category  social"], Category.SOCIAL),
        (["Category Updates"], Category.UPDATES),
        (["Category Forums"], Category.FORUMS),
        (["Inbox", "Important", "Category Personal"], Category.PRIMARY),
        (["Sent"], Category.PRIMARY),
        ([], Category.PRIMARY),
        (["Category Receipts"], Category.PRIMARY),
    ],
    ids=["promotions", "social", "updates", "forums", "personal", "sent", "none", "unknown"],
)
def test_a_messages_category_is_read_from_its_labels_by_name(labels: list[str], expected: Category):
    assert category(mail(labels)) is expected


def test_promotions_and_social_are_skipped_and_updates_and_forums_checked():
    assert {Category.PROMOTIONS, Category.SOCIAL} == SKIPPED
    assert {Category.UPDATES, Category.FORUMS} == CHECKED
    assert Category.PRIMARY not in SKIPPED | CHECKED


@pytest.mark.parametrize("corrected_first", [False, True], ids=["in-order", "correction-first"])
def test_only_the_latest_version_of_a_daily_note_by_capture_time_is_kept(corrected_first: bool):
    original = note("2026-10-06T01:00:00-05:00")
    correction = note("2026-10-06T08:10:00-05:00", b"Saturday mornings work best")
    email = mail([])
    envelopes = [correction, email, original] if corrected_first else [original, email, correction]

    assert latest_note_versions(envelopes) == [
        e for e in envelopes if e.episode_id != original.episode_id
    ]


def test_versions_captured_at_the_same_time_are_ordered_by_episode_id():
    one = note("2026-10-06T01:00:00-05:00")
    other = note("2026-10-06T01:00:00-05:00", b"Saturday mornings work best")

    [kept] = latest_note_versions([one, other])

    assert kept.episode_id == max(one.episode_id, other.episode_id)
