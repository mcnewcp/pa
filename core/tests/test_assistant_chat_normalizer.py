import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pa_core.envelope import Kind, Participant, Role, Source
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers.assistant_chat import normalize_assistant_chat
from pa_core.owner import Owner

FIXTURES = Path(__file__).parent / "fixtures" / "assistant_chat"
CAPTURED_AT = datetime(2026, 10, 8, 2, 0, tzinfo=UTC)
SINGLE_EXCHANGE = (FIXTURES / "single_exchange.json").read_bytes()
ASKED_A_QUESTION = (FIXTURES / "asked_a_question.json").read_bytes()
OWNER = Owner(
    name="Argus McNevans",
    email_addresses=("argus@example.com", "argus.mcnevans@example.org"),
    other_names=("Gus",),
)
SESSION_ID = "5b0f3c2e-8d41-4a7e-9c16-2f7d0a9e4b13"


def normalize(raw: bytes = SINGLE_EXCHANGE):
    return normalize_assistant_chat(raw, captured_at=CAPTURED_AT, owner=OWNER)


def test_an_exchange_is_a_chat_from_assistant_chat_whose_only_participant_is_the_owner():
    envelope = normalize()

    assert envelope.source == Source.ASSISTANT_CHAT
    assert envelope.kind == Kind.CHAT
    assert envelope.participants == [
        Participant(identifier="argus@example.com", name="Argus McNevans", role=Role.AUTHOR)
    ]
    assert envelope.subject is None


def test_occurred_at_runs_from_the_owners_message_to_the_end_of_the_reply():
    envelope = normalize()

    assert envelope.occurred_at.model_dump(mode="json") == {
        "start": "2026-10-08T01:12:04.512000Z",
        "end": "2026-10-08T01:12:31.907000Z",
    }


def test_the_session_is_the_thread_and_with_the_owners_message_makes_the_id():
    envelope = normalize()

    assert envelope.thread_ref == SESSION_ID
    assert envelope.native_id == "c4e1a7d2-3b9f-4e58-a061-7d2c9b8e1f40"
    assert envelope.episode_id == "assistant_chat_532dd6398f1dd884"
    assert envelope.raw_ref == "raw/assistant_chat/2026-10/assistant_chat_532dd6398f1dd884.json"


def test_the_body_is_the_owners_message_and_the_reply_labelled_by_role():
    envelope = normalize()

    assert envelope.body == (
        "owner: When are Theo and June's swim lessons?\n"
        "\n"
        "assistant: Saturdays at 9:00 at the YMCA, starting October 17 "
        "[ep:icloud_calendar_1a2b3c4d5e6f7a8b]."
    )


def test_a_question_the_assistant_asked_and_the_owners_answer_are_kept_in_order():
    envelope = normalize(ASKED_A_QUESTION)

    assert envelope.body == (
        "owner: Remind me what we decided about the kitchen.\n"
        "\n"
        "assistant: Do you mean the countertop quote or the cabinet colour?\n"
        "\n"
        "owner: The countertop quote.\n"
        "\n"
        "assistant: You went with Birchwood's quote of $4,200 [ep:gmail_0123456789abcdef].\n"
        "Rick's quote was higher [ep:gmail_fedcba9876543210]."
    )


def test_two_exchanges_of_one_session_share_the_thread_and_have_different_ids():
    first = normalize(SINGLE_EXCHANGE)
    second = normalize(ASKED_A_QUESTION)

    assert first.thread_ref == second.thread_ref == SESSION_ID
    assert first.episode_id != second.episode_id
    assert second.episode_id == "assistant_chat_5ff42672883619b5"


def test_a_recapture_of_the_same_exchange_is_the_same_episode_with_the_same_content():
    first = normalize()
    second = normalize_assistant_chat(
        SINGLE_EXCHANGE, captured_at=datetime(2026, 10, 9, tzinfo=UTC), owner=OWNER
    )

    assert second.episode_id == first.episode_id
    assert second.content_hash == first.content_hash


def edited(edit: Callable[[dict[str, Any]], None]) -> bytes:
    payload = json.loads(SINGLE_EXCHANGE)
    edit(payload)
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (edited(lambda p: p.update(source="gmail")), "unknown field 'source'"),
        (edited(lambda p: p["turns"][0].update(tool_use={})), "unknown field 'turns.0.tool_use'"),
        (edited(lambda p: p.pop("message_id")), "missing field 'message_id'"),
        (edited(lambda p: p["turns"][1].pop("text")), "missing field 'turns.1.text'"),
        (edited(lambda p: p["turns"][1].update(role="tool")), "turns.1.role"),
        (edited(lambda p: p["turns"][1].update(role="user")), "'owner' or 'assistant'"),
        (edited(lambda p: p.update(turns=[])), "turns: List should have at least 1 item"),
        (edited(lambda p: p.update(turns=None)), "turns"),
        (edited(lambda p: p.update(message_id=42)), "message_id"),
        (edited(lambda p: p.update(session_id="")), "session_id"),
        (edited(lambda p: p.update(started_at="2026-10-08T01:12:04")), "started_at"),
        (edited(lambda p: p.update(ended_at="2026-10-08T01:12:00Z")), "ends before it starts"),
        (b"[]", "should be an object"),
        (b"owner: hello", "Invalid JSON"),
        (SINGLE_EXCHANGE.replace(b"swim", "sw\xefm".encode("latin-1")), "Invalid JSON"),
    ],
    ids=[
        "unknown-field",
        "unknown-turn-field",
        "missing-field",
        "missing-turn-field",
        "tool-role",
        "user-role",
        "no-turns",
        "null-turns",
        "numeric-id",
        "empty-session",
        "naive-time",
        "ends-before-start",
        "not-an-object",
        "not-json",
        "not-utf8",
    ],
)
def test_a_payload_that_does_not_match_the_schema_exactly_is_malformed(raw: bytes, reason: str):
    with pytest.raises(MalformedPayloadError, match="assistant_chat payload") as error:
        normalize(raw)

    assert reason in str(error.value)
