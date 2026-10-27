"""Exchange capture: the exchange a turn of a Claude Code session made, from the turn's hook events.

A turn fires `UserPromptSubmit` with the owner's prompt, `PostToolUse` after each
`AskUserQuestion` with the owner's answers, and `Stop` once the assistant has replied, with the
reply. Each carries the session's id and the turn's `prompt_id`. The capture helper is handed
each event in turn (ADR-0009): it holds the prompt as pending for its session, adds each
question asked and answered, and at the stop writes the whole exchange into the inbox. Nothing
here reads the session's transcript, which the assistant's own process writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from pa_core.errors import MalformedPayloadError, PaError
from pa_core.normalizers.assistant_chat import (
    EXTENSION,
    TurnRole,
    encode_exchange,
    exchange_episode_id,
)

CAPTURE_VAR = "PA_CAPTURE_EXCHANGES"
"""Turns capture on for a session when set to 1 in its environment (the managed hooks check it).
Only the Remote Control unit sets it."""
CAPTURE_VARS = (CAPTURE_VAR,)
"""Every variable that configures capture; a session's environment without them captures nothing."""

VERBS = ("prompt", "answer", "stop")
"""What the helper is asked to do with an event: one verb per capture hook."""
_EVENTS = {"prompt": "UserPromptSubmit", "answer": "PostToolUse", "stop": "Stop"}

ASK_USER_QUESTION = "AskUserQuestion"
"""The tool the assistant asks the owner questions through; its result holds their answers."""

REFUSED_OPENINGS = {
    "<task-notification>": "a background task's report",
    "<cross-session-message": "another session's message",
}
"""How the prompts `UserPromptSubmit` fires for, but nobody typed, open (#81). The paths that
make them are closed before a session starts (the managed settings); the helper refuses them as
well. It can't tell a scheduled prompt from a typed one, so the denials are the fence."""

PENDING_TTL = timedelta(hours=24)
"""How long a prompt is held for its turn's stop. An interrupted turn fires no stop, so its
prompt is dropped by the session's next prompt or, failing that, once it's this old. Long
enough for a question the assistant asked to wait for the owner's answer."""

MAX_HOOK_INPUT = 1 << 20
"""The most the helper reads of an event; a longer one is refused whole."""


class CaptureRefusedError(PaError):
    """An event the helper won't take: malformed, too long, or for another hook."""


@dataclass(frozen=True)
class CapturedExchange:
    """One exchange as an `assistant_chat` raw payload, ready to drop into the inbox."""

    session_id: str
    message_id: str
    payload: bytes

    @property
    def file_name(self) -> str:
        """The payload's name in the inbox: its episode's id, the same on every recapture."""
        return exchange_episode_id(self.session_id, self.message_id) + EXTENSION


def _now() -> datetime:
    return datetime.now(UTC)


class ExchangeCapture:
    """The capture helper's work: pending prompts in `state`, finished exchanges in `inbox`."""

    def __init__(self, *, inbox: Path, state: Path, clock: Callable[[], datetime] = _now) -> None:
        self.inbox = inbox
        self.pending = state / "pending"
        self.clock = clock

    def handle(self, verb: str, event: Mapping[str, Any]) -> str | None:
        """Takes one hook's event; returns a note on why nothing was held or written, if so.

        Raises CaptureRefusedError for an event that isn't the verb's hook's, or is malformed.
        """
        if verb not in VERBS:
            raise CaptureRefusedError(f"no verb {verb!r}: one of {', '.join(VERBS)}")
        if event.get("hook_event_name") != _EVENTS[verb]:
            raise CaptureRefusedError(f"{verb} takes a {_EVENTS[verb]} event")
        session_id = _text(event, "session_id")
        if verb == "answer":
            # A tool's event may not carry the turn's prompt_id; when it does, it must match.
            prompt_id = event.get("prompt_id")
            self._expire()
            return self._answer(session_id, prompt_id, event)
        prompt_id = _text(event, "prompt_id")
        self._expire()
        if verb == "prompt":
            return self._prompt(session_id, prompt_id, event)
        return self._stop(session_id, prompt_id, event)

    def _prompt(self, session_id: str, prompt_id: str, event: Mapping[str, Any]) -> str | None:
        prompt = event.get("prompt")
        if not isinstance(prompt, str):
            raise CaptureRefusedError("the event has no prompt")
        text = prompt.strip()
        self._pending_path(session_id).unlink(missing_ok=True)
        if not text:
            return "nothing held: the prompt has no text"
        for opening, what in REFUSED_OPENINGS.items():
            if text.startswith(opening):
                return f"refused a prompt that opens as {what}: nobody typed it"
        self._hold(
            session_id,
            {
                "prompt_id": prompt_id,
                "started_at": self._timestamp(),
                "turns": [["owner", text]],
            },
        )
        return None

    def _answer(self, session_id: str, prompt_id: object, event: Mapping[str, Any]) -> str | None:
        """Adds each question the assistant asked and the owner answered, in the order asked.

        A question the owner dismissed without answering is left out.
        """
        if event.get("tool_name") != ASK_USER_QUESTION:
            raise CaptureRefusedError(f"answer takes {ASK_USER_QUESTION}'s results only")
        pending = self._held(session_id)
        if pending is None or prompt_id not in (None, pending.get("prompt_id")):
            return "nothing added: no prompt of the owner's is pending for this turn"
        tool_input, response = event.get("tool_input"), event.get("tool_response")
        if not isinstance(tool_input, dict) or not isinstance(response, dict):
            raise CaptureRefusedError(f"the event has no {ASK_USER_QUESTION} call and result")
        questions, answers = tool_input.get("questions"), response.get("answers") or {}
        if not isinstance(questions, list) or not isinstance(answers, dict):
            raise CaptureRefusedError(f"the event's {ASK_USER_QUESTION} call is malformed")
        for item in questions:
            question = item.get("question") if isinstance(item, dict) else None
            answer = _answer_text(answers.get(question)) if isinstance(question, str) else None
            if question and question.strip() and answer:
                pending["turns"] += [["assistant", question.strip()], ["owner", answer]]
        self._hold(session_id, pending)
        return None

    def _stop(self, session_id: str, prompt_id: str, event: Mapping[str, Any]) -> str | None:
        reply = event.get("last_assistant_message")
        pending = self._held(session_id)
        self._pending_path(session_id).unlink(missing_ok=True)
        if pending is None or pending.get("prompt_id") != prompt_id:
            return "nothing captured: no prompt of the owner's is pending for this turn"
        if not isinstance(reply, str) or not reply.strip():
            return "nothing captured: the turn ended without a reply"
        turns: list[tuple[TurnRole, str]] = [(role, text) for role, text in pending["turns"]]
        turns.append(("assistant", reply.strip()))
        payload = encode_exchange(
            session_id=session_id,
            message_id=prompt_id,
            started_at=pending["started_at"],
            ended_at=self._timestamp(),
            turns=turns,
        )
        _drop(CapturedExchange(session_id, prompt_id, payload), self.inbox)
        return None

    def _timestamp(self) -> str:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("the capture clock must give a UTC offset")
        return now.astimezone(UTC).isoformat(timespec="milliseconds")

    def _expire(self) -> None:
        """Drops every pending prompt held longer than PENDING_TTL, and any unreadable one."""
        oldest = self.clock() - PENDING_TTL
        for path in self.pending.glob("*.json") if self.pending.is_dir() else ():
            try:
                held = datetime.fromisoformat(json.loads(path.read_text())["started_at"])
                if held >= oldest:
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                pass
            path.unlink(missing_ok=True)

    def _pending_path(self, session_id: str) -> Path:
        # Named by a hash: a session id is the event's, and never becomes a path.
        return self.pending / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")

    def _held(self, session_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._pending_path(session_id).read_text())
        except FileNotFoundError:
            return None

    def _hold(self, session_id: str, pending: dict[str, Any]) -> None:
        self.pending.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_atomically(self._pending_path(session_id), json.dumps(pending).encode(), mode=0o600)


def _answer_text(answer: object) -> str | None:
    """The owner's answer as text: several choices joined, and None for no answer."""
    if isinstance(answer, list):
        answer = ", ".join(str(choice) for choice in answer)
    return (answer.strip() or None) if isinstance(answer, str) else None


def _text(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CaptureRefusedError(f"the event has no {field}")
    return value


def _drop(exchange: CapturedExchange, inbox: Path) -> None:
    """Writes the payload into `inbox` under a hidden name, then renames it into place.

    Mode 0644: ingest, on the devbox, reads it as the inbox directory's owner.
    """
    if not inbox.is_dir():
        raise CaptureRefusedError(f"the inbox directory {inbox} is missing (is it mounted?)")
    _write_atomically(inbox / exchange.file_name, exchange.payload, mode=0o644)


def _write_atomically(target: Path, content: bytes, *, mode: int) -> None:
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        partial.chmod(mode)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def run_helper(argv: list[str], stdin: BinaryIO, *, clock: Callable[[], datetime] = _now) -> int:
    """The capture helper: `VERB --inbox DIR --state DIR`, with the hook's event on stdin.

    Returns the hook's exit status: 0, with a note on stderr when nothing was held or written,
    or 1 with the reason on stderr, which Claude Code shows without stopping the session. Never
    2, which would block the owner's prompt or make the assistant carry on talking. Prints
    nothing on stdout, which Claude Code would add to the owner's prompt.
    """
    parser = argparse.ArgumentParser(prog="pa-capture", add_help=False)
    parser.add_argument("verb")
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print("exchange capture: usage: VERB --inbox DIR --state DIR", file=sys.stderr)
        return 1
    try:
        raw = stdin.read(MAX_HOOK_INPUT + 1)
        if len(raw) > MAX_HOOK_INPUT:
            raise CaptureRefusedError(f"the event is longer than {MAX_HOOK_INPUT} bytes")
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CaptureRefusedError("the event isn't JSON") from None
        if not isinstance(event, dict):
            raise CaptureRefusedError("the event isn't a JSON object")
        capture = ExchangeCapture(inbox=args.inbox, state=args.state, clock=clock)
        note = capture.handle(args.verb, event)
    except (CaptureRefusedError, MalformedPayloadError, OSError, ValueError) as error:
        print(f"exchange capture: {error}", file=sys.stderr)
        return 1
    if note:
        print(f"exchange capture: {note}", file=sys.stderr)
    return 0
