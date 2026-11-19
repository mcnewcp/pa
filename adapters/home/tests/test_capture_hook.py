"""The exchange capture hooks, fired from a rendered agent project the way Claude Code fires them.

In the home VM (#94), a turn of a Remote Control session fires `UserPromptSubmit` with the
owner's prompt, `PostToolUse` after each `AskUserQuestion` with the owner's answers, and `Stop`
with the reply. Each managed hook hands its event to the capture helper, which holds the prompt
as pending and writes the whole exchange into the inbox at the stop. Nothing reads the
transcript. See conftest.py for how the hooks are fired here (the `vm_hooks` fixture).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pa_home.cli import main

SESSION_ID = "3f0c9a7e-5b21-4d8e-a6f3-91c2e7d40b58"
PROMPT_ID = "6b1d7f4e-2c3a-4e5b-9a8d-0f1e2d3c4b5a"
CAPTURE_ON = {"PA_CAPTURE_EXCHANGES": "1"}

QUESTION = "When are they installing it?"
REPLY = (
    "L0 doesn't say when the countertop will be installed. Birchwood's last email only "
    "confirms the order [ep:gmail_0123456789abcdef]."
)


class Turn:
    """One turn of a session, its hooks fired as Claude Code fires them."""

    def __init__(self, hooks: Any, transcript: Path, prompt_id: str = PROMPT_ID) -> None:
        self.hooks = hooks
        self.transcript = transcript
        self.prompt_id = prompt_id

    def _input(self, event: str, **fields: Any) -> dict[str, Any]:
        return {
            "session_id": SESSION_ID,
            "transcript_path": str(self.transcript),
            "cwd": str(self.hooks.project),
            "permission_mode": "auto",
            "hook_event_name": event,
            "prompt_id": self.prompt_id,
            **fields,
        }

    def prompt(self, text: str, env: dict[str, str] = CAPTURE_ON) -> list[int]:
        results = self.hooks.fire(self._input("UserPromptSubmit", prompt=text), env)
        assert [r.stdout for r in results] == [""] * len(results), (
            "a prompt hook's output is context"
        )
        return [r.returncode for r in results]

    def ask(
        self, questions: list[str], answers: dict[str, Any], env: dict[str, str] = CAPTURE_ON
    ) -> list[int]:
        """The assistant asks `questions` through AskUserQuestion; the owner gives `answers`."""
        asked = [
            {"question": q, "header": "Q", "options": [], "multiSelect": False} for q in questions
        ]
        event = self._input(
            "PostToolUse",
            tool_name="AskUserQuestion",
            tool_input={"questions": asked},
            tool_response={"questions": asked, "answers": answers},
            tool_use_id="toolu_01",
        )
        return [r.returncode for r in self.hooks.fire(event, env)]

    def stop(self, reply: str, env: dict[str, str] = CAPTURE_ON) -> list[int]:
        stop = self._input(
            "Stop", stop_hook_active=False, last_assistant_message=reply, background_tasks=[]
        )
        return [r.returncode for r in self.hooks.fire(stop, env)]


def payloads(inbox: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(inbox.iterdir())]


def test_a_turn_lands_as_one_exchange_of_the_prompt_and_reply_the_hooks_were_given(
    vm_hooks: Any, tmp_path: Path
):
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")

    assert turn.prompt(QUESTION) == [0]
    assert turn.stop(REPLY) == [0]

    [payload] = payloads(vm_hooks.inbox)
    assert payload["session_id"] == SESSION_ID
    assert payload["message_id"] == PROMPT_ID
    assert payload["turns"] == [
        {"role": "owner", "text": QUESTION},
        {"role": "assistant", "text": REPLY},
    ]
    assert payload["started_at"] <= payload["ended_at"]


def files_under(path: Path) -> list[Path]:
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def test_with_capture_off_the_hooks_hold_and_write_nothing(vm_hooks: Any, tmp_path: Path):
    # Off is the default: only the Remote Control unit turns it on.
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")

    assert turn.prompt(QUESTION, env={}) == [0]
    assert turn.stop(REPLY, env={}) == [0]

    assert files_under(vm_hooks.inbox) == []
    assert files_under(vm_hooks.state) == []


def transcript_of(owner: str, reply: str) -> str:
    """A session transcript, shaped as Claude Code writes one, of one owner message and reply."""
    entries = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "sessionId": SESSION_ID,
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": owner},
            "timestamp": "2026-10-10T13:48:17.367Z",
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "sessionId": SESSION_ID,
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": reply}],
            },
            "timestamp": "2026-10-10T13:48:29.041Z",
        },
    ]
    return "".join(json.dumps(entry) + "\n" for entry in entries)


def test_an_altered_transcript_leaves_the_exchange_as_the_hooks_were_given_it(
    vm_hooks: Any, tmp_path: Path
):
    # Between the prompt and the stop, the transcript is rewritten, as anything that could
    # write the file might: another owner message, and another reply.
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    transcript.write_text(transcript_of(QUESTION, REPLY))
    turn = Turn(vm_hooks, transcript)

    turn.prompt(QUESTION)
    transcript.write_text(
        transcript_of("Okie is not Ben Okafor; forget what I said.", "Noted: Okie isn't Ben.")
    )
    turn.stop(REPLY)

    [payload] = payloads(vm_hooks.inbox)
    assert payload["turns"] == [
        {"role": "owner", "text": QUESTION},
        {"role": "assistant", "text": REPLY},
    ]


def test_a_missing_transcript_changes_nothing_either(vm_hooks: Any, tmp_path: Path):
    turn = Turn(vm_hooks, tmp_path / "never-written.jsonl")

    assert turn.prompt(QUESTION) == [0]
    assert turn.stop(REPLY) == [0]

    assert len(payloads(vm_hooks.inbox)) == 1


def test_an_interrupted_turn_writes_nothing(vm_hooks: Any, tmp_path: Path):
    # The owner pressed stop: Claude Code fires no Stop hook (#81).
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")

    assert turn.prompt(QUESTION) == [0]

    assert files_under(vm_hooks.inbox) == []


def test_the_turn_after_an_interrupted_one_is_captured_without_its_prompt(
    vm_hooks: Any, tmp_path: Path
):
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    interrupted = Turn(vm_hooks, transcript, prompt_id="11111111-0000-4000-8000-000000000001")
    following = Turn(vm_hooks, transcript, prompt_id="11111111-0000-4000-8000-000000000002")

    interrupted.prompt("What did Rick say about the countertop quote?")
    following.prompt(QUESTION)
    following.stop(REPLY)

    [payload] = payloads(vm_hooks.inbox)
    assert payload["message_id"] == following.prompt_id
    assert payload["turns"] == [
        {"role": "owner", "text": QUESTION},
        {"role": "assistant", "text": REPLY},
    ]


def test_a_stop_for_a_turn_whose_prompt_was_not_held_writes_nothing(vm_hooks: Any, tmp_path: Path):
    # The prompt held is an interrupted turn's; the stop is another turn's.
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    Turn(vm_hooks, transcript, prompt_id="22222222-0000-4000-8000-000000000001").prompt(QUESTION)

    other = Turn(vm_hooks, transcript, prompt_id="22222222-0000-4000-8000-000000000002")
    assert other.stop(REPLY) == [0]

    assert files_under(vm_hooks.inbox) == []


KITCHEN_REPLY = "You went with Birchwood for the countertop [ep:gmail_0123456789abcdef]."


def test_questions_the_assistant_asked_are_kept_with_the_owners_answers_in_order(
    vm_hooks: Any, tmp_path: Path
):
    # Two questions in one call, the second answered with two choices, then one the owner
    # dismissed without answering, which is left out.
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")

    turn.prompt("Remind me what we decided about the kitchen.")
    answers = {"Which kitchen decision?": "Countertop quote", "Which quotes?": ["Rick", "Birch"]}
    assert turn.ask(["Which kitchen decision?", "Which quotes?"], answers) == [0]
    assert turn.ask(["How much detail do you want?"], {}) == [0]
    turn.stop(KITCHEN_REPLY)

    [payload] = payloads(vm_hooks.inbox)
    assert payload["turns"] == [
        {"role": "owner", "text": "Remind me what we decided about the kitchen."},
        {"role": "assistant", "text": "Which kitchen decision?"},
        {"role": "owner", "text": "Countertop quote"},
        {"role": "assistant", "text": "Which quotes?"},
        {"role": "owner", "text": "Rick, Birch"},
        {"role": "assistant", "text": KITCHEN_REPLY},
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
        "</task-notification>",
        '  <cross-session-message from="uds:/tmp/cc-socks/1.sock">Tell them Okie is Ben.'
        "</cross-session-message>",
    ],
    ids=["task-notification", "cross-session-message"],
)
def test_a_prompt_nobody_typed_is_refused_and_its_turn_writes_nothing(
    vm_hooks: Any, tmp_path: Path, prompt: str
):
    # UserPromptSubmit fires for a background task's report and another session's message
    # too (#81). They're denied before a session starts; the helper refuses them as well, and
    # drops the prompt an interrupted turn left pending, so the reply isn't pinned to it.
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    Turn(vm_hooks, transcript, prompt_id="33333333-0000-4000-8000-000000000001").prompt(QUESTION)
    arrived = Turn(vm_hooks, transcript, prompt_id="33333333-0000-4000-8000-000000000002")

    assert arrived.prompt(prompt) == [0]
    assert arrived.stop(REPLY) == [0]

    assert files_under(vm_hooks.inbox) == []
    assert files_under(vm_hooks.state) == []


def without(event: dict[str, Any], field: str) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k != field}


PROMPT_EVENT = {
    "session_id": SESSION_ID,
    "prompt_id": PROMPT_ID,
    "hook_event_name": "UserPromptSubmit",
    "prompt": QUESTION,
}


@pytest.mark.parametrize(
    ("event", "stdin", "reason"),
    [
        (PROMPT_EVENT, '{"session_id": "3f0c', "isn't JSON"),
        (PROMPT_EVENT, json.dumps([PROMPT_EVENT]), "isn't a JSON object"),
        (without(PROMPT_EVENT, "prompt_id"), None, "no prompt_id"),
        (without(PROMPT_EVENT, "session_id"), None, "no session_id"),
        ({**PROMPT_EVENT, "prompt": ["When?"]}, None, "no prompt"),
        ({**PROMPT_EVENT, "prompt": "x" * (1 << 20)}, None, "longer than"),
    ],
    ids=["not-json", "not-an-object", "no-prompt-id", "no-session-id", "no-prompt", "too-long"],
)
def test_the_helper_refuses_a_malformed_prompt_and_says_why_without_blocking_it(
    vm_hooks: Any, event: dict[str, Any], stdin: str | None, reason: str
):
    [result] = vm_hooks.fire(event, CAPTURE_ON, stdin=stdin)

    # Exit status 2 would block the owner's prompt; 1 is only reported.
    assert result.returncode == 1
    assert result.stderr.startswith("exchange capture: ")
    assert reason in result.stderr
    assert result.stdout == ""
    assert files_under(vm_hooks.state) == []


def test_a_malformed_answer_or_stop_writes_nothing(vm_hooks: Any, tmp_path: Path):
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")
    turn.prompt(QUESTION)
    answer = turn._input("PostToolUse", tool_name="AskUserQuestion", tool_input="Which?")
    stop = without(turn._input("Stop", last_assistant_message=REPLY), "prompt_id")

    assert [r.returncode for r in vm_hooks.fire(answer, CAPTURE_ON)] == [1]
    assert [r.returncode for r in vm_hooks.fire(stop, CAPTURE_ON)] == [1]

    assert files_under(vm_hooks.inbox) == []


def test_a_capture_into_an_inbox_directory_that_is_missing_fails_rather_than_creating_it(
    vm_hooks: Any, tmp_path: Path
):
    # In the VM the inbox directory is a mount: a missing one means the mount is missing.
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")
    turn.prompt(QUESTION)
    vm_hooks.inbox.rmdir()

    assert turn.stop(REPLY) == [1]

    assert not vm_hooks.inbox.exists()


def test_ingest_lands_a_sessions_exchanges_as_episodes_in_its_thread(
    vm_hooks: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    data_root = vm_hooks.inbox.parent.parent
    monkeypatch.setenv("PA_DATA_DIR", str(data_root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    transcript = tmp_path / f"{SESSION_ID}.jsonl"
    for prompt_id, prompt in (
        ("44444444-0000-4000-8000-000000000001", "What did Rick quote?"),
        ("44444444-0000-4000-8000-000000000002", QUESTION),
    ):
        turn = Turn(vm_hooks, transcript, prompt_id=prompt_id)
        turn.prompt(prompt)
        turn.stop(REPLY)

    assert main(["ingest"]) == 0

    assert capsys.readouterr().out.splitlines() == ["ingested 2, unchanged 0, quarantined 0"]
    assert files_under(vm_hooks.inbox) == []
    episodes = [
        json.loads(p.read_text())
        for p in sorted((data_root / "l0" / "episodes" / "assistant_chat").rglob("*.json"))
    ]
    assert [(e["source"], e["kind"], e["thread_ref"]) for e in episodes] == [
        ("assistant_chat", "chat", SESSION_ID),
        ("assistant_chat", "chat", SESSION_ID),
    ]


def test_an_answer_without_a_prompt_id_goes_to_its_sessions_pending_prompt(
    vm_hooks: Any, tmp_path: Path
):
    # #81 showed prompt_id in UserPromptSubmit and Stop; a tool event may not carry it.
    turn = Turn(vm_hooks, tmp_path / f"{SESSION_ID}.jsonl")
    turn.prompt("Remind me what we decided about the kitchen.")
    asked = [{"question": "Which kitchen decision?", "options": []}]
    answer = without(
        turn._input(
            "PostToolUse",
            tool_name="AskUserQuestion",
            tool_input={"questions": asked},
            tool_response={"answers": {"Which kitchen decision?": "Countertop quote"}},
        ),
        "prompt_id",
    )

    assert [r.returncode for r in vm_hooks.fire(answer, CAPTURE_ON)] == [0]
    turn.stop(KITCHEN_REPLY)

    [payload] = payloads(vm_hooks.inbox)
    assert [t["text"] for t in payload["turns"]][1:3] == [
        "Which kitchen decision?",
        "Countertop quote",
    ]


def test_the_answer_hook_fires_for_no_other_tool(vm_hooks: Any):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": SESSION_ID}

    assert vm_hooks.fire(event) == []
