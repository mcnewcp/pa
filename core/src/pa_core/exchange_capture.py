"""Exchange capture: the exchange that just ended in a Claude Code session, as a raw payload.

Claude Code writes each session as a JSONL transcript: one entry per message block, tool result,
attachment or bookkeeping record, each user and assistant entry linked to the one before it by
`parentUuid`. When the assistant stops, the exchange that just ended runs from the first owner
message after the previous reply to the reply just given.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_core.envelope import Source, episode_id
from pa_core.errors import PaError
from pa_core.normalizers.assistant_chat import EXTENSION

CAPTURE_VAR = "PA_CAPTURE_EXCHANGES"
"""Turns capture on for a session when set to 1 in its environment. Off when unset."""
INBOX_VAR = "PA_CAPTURE_INBOX"
"""The inbox directory captured exchanges go into (`inbox/assistant_chat/`)."""
CAPTURE_VARS = (CAPTURE_VAR, INBOX_VAR)
TRANSCRIPT_WAIT_SECONDS = 10.0
"""How long the hook waits for Claude Code to write the reply to the transcript."""
_TRANSCRIPT_POLL_SECONDS = 0.1

_Entry = dict[str, Any]

ASK_USER_QUESTION = "AskUserQuestion"
"""The tool the assistant asks the owner questions through; its result holds their answers."""

_REPLY_ENDS = frozenset({"end_turn", "stop_sequence", "max_tokens", "refusal"})
"""Stop reasons that end the assistant's reply; `tool_use` (and none, mid-stream) don't."""


@dataclass(frozen=True)
class CapturedExchange:
    """One exchange as an `assistant_chat` raw payload, ready to drop into the inbox."""

    session_id: str
    message_id: str
    payload: bytes

    @property
    def file_name(self) -> str:
        """The payload's name in the inbox: its episode's id, the same on every recapture."""
        return episode_id(Source.ASSISTANT_CHAT, self.session_id, self.message_id) + EXTENSION


class ReplyNotInTranscriptError(PaError):
    """The transcript doesn't hold the reply the assistant just gave, or not all of it yet."""


class CaptureConfigError(PaError):
    """Capture is turned on, but not configured so it can work."""


def capture_exchange(transcript: str, *, final_reply: str | None) -> CapturedExchange | None:
    """The exchange that ends with the assistant's latest reply in `transcript`.

    Keeps the owner's messages, each question the assistant asked the owner through a tool with
    the owner's answer, and the reply; leaves out tool calls and results, thinking, and the
    assistant's text around tool calls. None when the turn that just ended didn't start with a
    message from the owner (a shell command run from the prompt, a background task's report).

    `final_reply` is the reply's text as Claude Code reports it at the stop (None or empty to
    skip the check). Claude Code writes the transcript asynchronously, so it may not hold that
    reply yet: then, or when the latest reply in the transcript doesn't end with that text,
    raises ReplyNotInTranscriptError.
    """
    chain = _active_chain(transcript)
    reply = _final_reply(chain)
    # Compared loosely: the reported text may be only the reply's last text block, joined
    # differently.
    if final_reply and (
        reply is None or not _squeezed(reply.text).endswith(_squeezed(final_reply))
    ):
        raise ReplyNotInTranscriptError(
            "the transcript doesn't end with the reply the assistant just gave (yet)"
        )
    if reply is None:
        return None
    turns: list[dict[str, str]] = []
    owner_message: _Entry | None = None
    questions: dict[str, list[str]] = {}
    for entry in chain[_exchange_start(chain, reply.start) : reply.start]:
        text = _owner_text(entry)
        if text:
            owner_message = owner_message or entry
            turns.append({"role": "owner", "text": text})
        questions.update(_questions_asked(entry))
        for question, answer in _answers(entry, questions):
            turns.append({"role": "assistant", "text": question})
            turns.append({"role": "owner", "text": answer})
    if owner_message is None:
        return None
    turns.append({"role": "assistant", "text": reply.text})
    payload = {
        "session_id": owner_message["sessionId"],
        "message_id": owner_message["uuid"],
        "started_at": owner_message["timestamp"],
        "ended_at": reply.ended_at,
        "turns": turns,
    }
    return CapturedExchange(
        session_id=payload["session_id"],
        message_id=payload["message_id"],
        payload=(json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(),
    )


def _active_chain(transcript: str) -> list[_Entry]:
    """The conversation's entries in order, following `parentUuid` back from the latest one."""
    by_uuid: dict[str, _Entry] = {}
    latest: _Entry | None = None
    for entry in _entries(transcript):
        if isinstance(entry.get("uuid"), str):
            by_uuid[entry["uuid"]] = entry
            latest = entry
    chain: list[_Entry] = []
    seen: set[str] = set()
    while latest is not None and latest["uuid"] not in seen:
        seen.add(latest["uuid"])
        chain.append(latest)
        parent = latest.get("parentUuid") or latest.get("logicalParentUuid")
        latest = by_uuid.get(parent) if isinstance(parent, str) else None
    chain.reverse()
    return chain


def _entries(transcript: str) -> Iterator[_Entry]:
    for line in transcript.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line still being written
        if isinstance(entry, dict):
            yield entry


@dataclass(frozen=True)
class _Reply:
    start: int
    """Where the reply's first entry is in the chain."""
    text: str
    ended_at: str


def _final_reply(chain: list[_Entry]) -> _Reply | None:
    """The reply the chain ends with, if it ends with one.

    One reply is one assistant message, written as one entry per content block.
    """
    end = len(chain)
    while end and chain[end - 1].get("type") not in ("user", "assistant"):
        end -= 1  # bookkeeping after the reply, such as the turn's duration
    if not end or not _ends_a_reply(chain[end - 1]):
        return None
    message_id = chain[end - 1]["message"].get("id")
    start = end
    while start and _is_assistant_message(chain[start - 1], message_id):
        start -= 1
    text = "\n\n".join(_texts(chain[start:end]))
    return _Reply(start, text, chain[end - 1]["timestamp"]) if text else None


def _squeezed(text: str) -> str:
    """`text` without whitespace, to compare texts that differ only in how blocks are joined."""
    return "".join(text.split())


def _exchange_start(chain: list[_Entry], reply_start: int) -> int:
    """Where the exchange ending at `reply_start` starts: just after the previous reply."""
    start = reply_start
    while start and not _ends_a_reply(chain[start - 1]):
        start -= 1
    return start


def _ends_a_reply(entry: _Entry) -> bool:
    return entry.get("type") == "assistant" and entry["message"].get("stop_reason") in _REPLY_ENDS


def _is_assistant_message(entry: _Entry, message_id: object) -> bool:
    return entry.get("type") == "assistant" and entry["message"].get("id") == message_id


def _texts(entries: list[_Entry]) -> list[str]:
    return [
        block["text"]
        for entry in entries
        for block in entry["message"].get("content", [])
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]


def _questions_asked(entry: _Entry) -> dict[str, list[str]]:
    """The questions in each AskUserQuestion call in `entry`, by tool call id."""
    if entry.get("type") != "assistant":
        return {}
    asked: dict[str, list[str]] = {}
    for block in entry["message"].get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") != ASK_USER_QUESTION:
            continue
        items = (block.get("input") or {}).get("questions") or []
        asked[block.get("id", "")] = [
            item["question"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("question"), str)
        ]
    return asked


def _answers(entry: _Entry, questions: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Each question asked through a tool that `entry` holds the owner's answer to, in order.

    A question the owner declined to answer is left out with its tool result.
    """
    if entry.get("type") != "user":
        return []
    content = entry["message"].get("content")
    result = entry.get("toolUseResult")
    if not isinstance(content, list) or not isinstance(result, dict):
        return []
    answers = result.get("answers")
    if not isinstance(answers, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        for question in questions.get(block.get("tool_use_id", ""), []):
            answer = _answer_text(answers.get(question))
            if answer:
                pairs.append((question, answer))
    return pairs


def _answer_text(answer: object) -> str | None:
    if isinstance(answer, list):
        answer = ", ".join(str(choice) for choice in answer)
    return (answer.strip() or None) if isinstance(answer, str) else None


def _owner_text(entry: _Entry) -> str | None:
    """The text of a message the owner wrote, or None for any other entry."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    if (entry.get("origin") or {}).get("kind") != "human":
        return None
    content = entry["message"].get("content")
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n\n".join(t for t in texts if t).strip() or None


def run_stop_hook(
    hook_input: bytes,
    environ: Mapping[str, str],
    *,
    wait_seconds: float = TRANSCRIPT_WAIT_SECONDS,
) -> int:
    """The `Stop` hook: drops the exchange that just ended into the inbox, if capture is on.

    `hook_input` is the JSON Claude Code passes on stdin. Capture is off unless `environ` sets
    PA_CAPTURE_EXCHANGES to 1, and the inbox directory comes from PA_CAPTURE_INBOX, never from
    the agent project. The payload is written under a hidden name, then renamed into place, so
    ingest never reads half of one. Waits up to `wait_seconds` for Claude Code to finish writing
    the reply to the transcript.

    Returns the hook's exit status: 0, or 1 with the reason on stderr, which Claude Code shows
    without stopping the session. Never 2, which would make the assistant carry on talking.
    """
    setting = environ.get(CAPTURE_VAR, "").strip()
    if setting in ("", "0"):
        return 0
    try:
        if setting != "1":
            raise CaptureConfigError(f"{CAPTURE_VAR} is 1 (on) or 0 (off), not {setting!r}")
        inbox = _inbox(environ)
        stop = json.loads(hook_input)
        transcript = Path(stop["transcript_path"])
        final_reply = stop.get("last_assistant_message")
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                exchange = capture_exchange(transcript.read_text(), final_reply=final_reply)
                break
            except ReplyNotInTranscriptError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_TRANSCRIPT_POLL_SECONDS)
        if exchange is not None:
            _drop(exchange, inbox)
    except (PaError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"exchange capture: {error}", file=sys.stderr)
        return 1
    return 0


def _inbox(environ: Mapping[str, str]) -> Path:
    value = environ.get(INBOX_VAR, "").strip()
    if not value:
        raise CaptureConfigError(f"capture is on but {INBOX_VAR} is not set")
    inbox = Path(value)
    if not inbox.is_absolute() or not inbox.is_dir():
        raise CaptureConfigError(f"{INBOX_VAR} ({value}) is not an existing absolute directory")
    return inbox


def _drop(exchange: CapturedExchange, inbox: Path) -> None:
    """Writes the payload into `inbox` under a hidden name, then renames it into place."""
    target = inbox / exchange.file_name
    partial = inbox / f".{exchange.file_name}.{os.getpid()}.partial"
    try:
        with partial.open("wb") as file:
            file.write(exchange.payload)
            file.flush()
            os.fsync(file.fileno())
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
