"""Exchange capture: from a Claude Code session transcript to the exchange that just ended.

The fixtures are transcripts recorded by Claude Code 2.1.291 and 2.1.293, trimmed, with every
text, tool call and tool result replaced by neutral text, ids remapped and paths neutralized.
Their structure (entry kinds, the parentUuid chain, origins, stop reasons) is as recorded. A
transcript cut off after an earlier reply is the transcript as it stood at that reply's stop.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.exchange_capture import ReplyNotInTranscriptError, capture_exchange
from pa_core.normalizers.assistant_chat import normalize_assistant_chat
from pa_core.owner import Owner

FIXTURES = Path(__file__).parent / "fixtures" / "claude_code_transcripts"
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
TWO_EXCHANGES = (FIXTURES / "two_exchanges.jsonl").read_text()

INSTALL_REPLY = (
    "L0 doesn't say when the countertop will be installed. Birchwood's last email only "
    "confirms the order [ep:gmail_0123456789abcdef]."
)


def captured(transcript: str, final_reply: str | None) -> dict:
    exchange = capture_exchange(transcript, final_reply=final_reply)
    assert exchange is not None
    return json.loads(exchange.payload)


def test_a_plain_question_and_reply_is_the_owners_message_and_the_reply():
    payload = captured(TWO_EXCHANGES, INSTALL_REPLY)

    assert payload == {
        "session_id": "3f0c9a7e-5b21-4d8e-a6f3-91c2e7d40b58",
        "message_id": "5c4965cc-023a-582d-870d-2abe0de87671",
        "started_at": "2026-10-10T13:48:17.367Z",
        "ended_at": "2026-10-10T13:48:29.041Z",
        "turns": [
            {"role": "owner", "text": "When are they installing it?"},
            {"role": "assistant", "text": INSTALL_REPLY},
        ],
    }


QUOTE_QUESTION = "What did Rick say about the countertop quote, and did we go with it?"
QUOTE_REPLY = (
    "Rick quoted $5,100 for quartz counters, installed [ep:gmail_fedcba9876543210].\n\n"
    "You went with Birchwood's quote of $4,200 instead [ep:gmail_0123456789abcdef]."
)


def until_line(transcript: str, count: int) -> str:
    """The first `count` lines of `transcript`: the transcript as it stood at an earlier stop."""
    return "".join(transcript.splitlines(keepends=True)[:count])


def test_tool_calls_their_results_and_thinking_are_left_out():
    # The first exchange: two Bash calls with their results, and thinking, before the reply.
    # Before it, the owner started a message, then went back and sent another one instead.
    at_first_stop = until_line(TWO_EXCHANGES, 23)

    payload = captured(at_first_stop, QUOTE_REPLY)

    assert payload == {
        "session_id": "3f0c9a7e-5b21-4d8e-a6f3-91c2e7d40b58",
        "message_id": "9409a111-697b-557f-8cd8-2dd0a6021284",
        "started_at": "2026-10-10T13:44:00.213Z",
        "ended_at": "2026-10-10T13:44:28.242Z",
        "turns": [
            {"role": "owner", "text": QUOTE_QUESTION},
            {"role": "assistant", "text": QUOTE_REPLY},
        ],
    }


ASKED_QUESTIONS = (FIXTURES / "asked_questions.jsonl").read_text()
KITCHEN_REPLY = (
    "You went with Birchwood for the countertop.\n\n"
    "1. Rick quoted $5,100 on September 3 [ep:gmail_fedcba9876543210].\n"
    "2. Birchwood quoted $4,200 on September 9, and you accepted it on September 12 "
    "[ep:gmail_0123456789abcdef]."
)


def test_questions_the_assistant_asked_through_a_tool_are_kept_with_the_owners_answers():
    # One AskUserQuestion call with two questions, then one with a single question that the
    # owner answered in their own words; tool calls before, between and after.
    payload = captured(ASKED_QUESTIONS, KITCHEN_REPLY)

    assert payload == {
        "session_id": "a2d7e4c1-0f68-4b93-8e15-6c3b9f2a7d04",
        "message_id": "5f331fbc-aa6a-55ca-a05b-d16c5abe793a",
        "started_at": "2026-10-11T08:02:48.296Z",
        "ended_at": "2026-10-11T08:03:31.728Z",
        "turns": [
            {"role": "owner", "text": "Remind me what we decided about the kitchen."},
            {"role": "assistant", "text": "Which kitchen decision do you mean?"},
            {"role": "owner", "text": "Countertop quote"},
            {"role": "assistant", "text": "How much detail do you want?"},
            {"role": "owner", "text": "The whole story"},
            {"role": "assistant", "text": "Should I include the quotes you turned down?"},
            {"role": "owner", "text": "Only Rick's"},
            {"role": "assistant", "text": KITCHEN_REPLY},
        ],
    }


DECLINED_QUESTION = (FIXTURES / "declined_question.jsonl").read_text()


def test_text_around_tool_calls_and_a_question_the_owner_declined_are_left_out():
    # Before asking, the assistant said what it was about to do; the owner then dismissed the
    # question without answering it.
    payload = captured(DECLINED_QUESTION, "Sure, what would you like to clarify about the swim?")

    assert payload == {
        "session_id": "c81e5b3a-7d24-4f09-b6a2-2e9d0c4f8a17",
        "message_id": "162e2a3b-5386-5156-bb29-a7f076883a67",
        "started_at": "2026-10-10T09:10:42.436Z",
        "ended_at": "2026-10-10T09:18:09.401Z",
        "turns": [
            {"role": "owner", "text": "Is swim on?"},
            {"role": "assistant", "text": "Sure, what would you like to clarify about the swim?"},
        ],
    }


def test_a_turn_the_owner_did_not_start_with_a_message_is_not_captured():
    # The owner ran a shell command (`!date`) and the assistant replied to its output.
    after_shell_command = until_line(DECLINED_QUESTION, 8)

    exchange = capture_exchange(
        after_shell_command,
        final_reply="It's Saturday, October 10, a little after 9:10 in the morning.",
    )

    assert exchange is None


def test_the_payload_is_an_assistant_chat_episode_in_the_sessions_thread_named_by_its_id():
    exchange = capture_exchange(ASKED_QUESTIONS, final_reply=KITCHEN_REPLY)
    assert exchange is not None

    envelope = normalize_assistant_chat(
        exchange.payload, captured_at=datetime(2026, 10, 11, 9, 0, tzinfo=UTC), owner=OWNER
    )

    assert envelope.thread_ref == "a2d7e4c1-0f68-4b93-8e15-6c3b9f2a7d04"
    assert envelope.native_id == "5f331fbc-aa6a-55ca-a05b-d16c5abe793a"
    assert exchange.file_name == f"{envelope.episode_id}.json"
    assert envelope.body.startswith(
        "owner: Remind me what we decided about the kitchen.\n\n"
        "assistant: Which kitchen decision do you mean?\n\n"
        "owner: Countertop quote\n\n"
    )


def test_the_two_exchanges_of_a_session_are_two_episodes_in_one_thread():
    first = capture_exchange(until_line(TWO_EXCHANGES, 23), final_reply=QUOTE_REPLY)
    second = capture_exchange(TWO_EXCHANGES, final_reply=INSTALL_REPLY)
    assert first is not None and second is not None

    first_episode, second_episode = (
        normalize_assistant_chat(e.payload, captured_at=datetime.now(UTC), owner=OWNER)
        for e in (first, second)
    )

    assert first_episode.thread_ref == second_episode.thread_ref
    assert first_episode.episode_id != second_episode.episode_id
    assert first.file_name != second.file_name


def test_capturing_an_exchange_again_gives_the_same_payload_byte_for_byte():
    # A second firing for the same reply may see bookkeeping Claude Code wrote since the first.
    at_the_stop = until_line(TWO_EXCHANGES, 23)
    a_moment_later = until_line(TWO_EXCHANGES, 24)

    first = capture_exchange(at_the_stop, final_reply=QUOTE_REPLY)
    again = capture_exchange(a_moment_later, final_reply=QUOTE_REPLY)

    assert first is not None and again is not None
    assert again.payload == first.payload
    assert again.file_name == first.file_name


@pytest.mark.parametrize(
    "lines",
    [25, 26, 27],
    ids=["owner-message-only", "attachment-after-it", "thinking-of-the-reply"],
)
def test_a_reply_not_yet_written_to_the_transcript_is_reported_rather_than_guessed(lines: int):
    # Claude Code writes the transcript asynchronously, so at the stop it may lag the reply.
    lagging = until_line(TWO_EXCHANGES, lines)

    with pytest.raises(ReplyNotInTranscriptError):
        capture_exchange(lagging, final_reply=INSTALL_REPLY)


def test_a_reply_other_than_the_one_reported_is_not_taken_for_it():
    with pytest.raises(ReplyNotInTranscriptError):
        capture_exchange(TWO_EXCHANGES, final_reply="Something the assistant never said.")
