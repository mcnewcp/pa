"""The agent runner interface: how the harness asks the agent one question.

A runner answers in free text and cites evidence as `[ep:<episode_id>]`, with what the answer
cost where it knows (dollars, turns, and duration) and the tool calls it made to get there.
Anything it raises (including AgentTimeoutError) is recorded as a failed result for that
question; the run goes on.
The harness cannot interrupt a runner, so a runner enforces its own timeout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pa_core.owner import Owner
from pa_evals.script import Script
from pa_home.agent import AgentAnswer, AgentError, AgentTimeoutError, ClaudeAgent, ToolCall
from pa_home.config import DataRoot

__all__ = [
    "AgentAnswer",
    "AgentError",
    "AgentRequest",
    "AgentRunner",
    "AgentTimeoutError",
    "ClaudeAgentRunner",
    "ScriptedAgent",
    "ToolCall",
]


@dataclass(frozen=True)
class AgentRequest:
    """One question for the assistant, asked from `as_of` against the throwaway
    `data_root`."""

    question_id: str
    question: str
    as_of: datetime
    """The assistant's "now" for this question."""
    data_root: DataRoot
    """The throwaway data root the corpus was ingested into (read-only for the assistant)."""
    owner: Owner
    wiki: Path | None = None
    """The eval wiki's read-only tree beside the throwaway L0, in a files-plus-wiki run; None
    in files alone."""
    status: Path | None = None
    """The read-only status file saying how current L0 and the wiki are: over a frozen corpus,
    one laid with every source's watermark at `as_of`; over a cut, the cut's own. None for a
    cut taken without one."""


class AgentRunner(Protocol):
    def answer(self, request: AgentRequest) -> AgentAnswer:
        """The assistant's answer, citing evidence as `[ep:<episode_id>]`."""
        ...


class ClaudeAgentRunner:
    """The real assistant (`claude -p` in the agent project), asked at the question's as-of time."""

    def __init__(self, agent: ClaudeAgent) -> None:
        self._agent = agent

    def answer(self, request: AgentRequest) -> AgentAnswer:
        return self._agent.answer(
            request.question,
            owner=request.owner,
            l0=request.data_root.l0,
            wiki=request.wiki,
            status=request.status,
            now=request.as_of,
        )


type ScriptedAnswer = str | AgentAnswer | Exception


class ScriptedAgent:
    """A fake agent that answers each question id with a scripted answer, or raises its error.

    A question id may have a list of answers instead, one per run, played in order. An answer
    scripted as text has no cost, turns, duration, or tool calls.

    Every request it receives is recorded in `requests`.
    """

    def __init__(self, answers: Mapping[str, ScriptedAnswer | list[ScriptedAnswer]]) -> None:
        self._answers = Script(answers)
        self.requests: list[AgentRequest] = []

    def answer(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        scripted = self._answers.next(request.question_id)
        if scripted is None:
            raise AgentError(f"no scripted answer for question {request.question_id}")
        if isinstance(scripted, Exception):
            raise scripted
        return AgentAnswer(scripted) if isinstance(scripted, str) else scripted
