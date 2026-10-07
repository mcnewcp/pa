"""The agent runner interface: how the harness asks the agent one question.

A runner answers in free text and cites evidence as `[ep:<episode_id>]`. Anything it raises
(including AgentTimeoutError) is recorded as a failed result for that question; the run goes on.
The harness cannot interrupt a runner, so a runner enforces its own timeout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pa_core.owner import Owner
from pa_home.agent import AgentError, AgentTimeoutError, ClaudeAgent
from pa_home.config import DataRoot

__all__ = [
    "AgentError",
    "AgentRequest",
    "AgentRunner",
    "AgentTimeoutError",
    "ClaudeAgentRunner",
    "ScriptedAgent",
]


@dataclass(frozen=True)
class AgentRequest:
    """One question for the agent, asked from `as_of` against the throwaway `data_root`."""

    question_id: str
    question: str
    as_of: datetime
    """The agent's "now" for this question."""
    data_root: DataRoot
    """The throwaway data root the corpus was ingested into (read-only for the agent)."""
    owner: Owner


class AgentRunner(Protocol):
    def answer(self, request: AgentRequest) -> str:
        """The agent's answer, citing evidence as `[ep:<episode_id>]`."""
        ...


class ClaudeAgentRunner:
    """The real agent (`claude -p` in the agent project), asked at the question's as-of time."""

    def __init__(self, agent: ClaudeAgent) -> None:
        self._agent = agent

    def answer(self, request: AgentRequest) -> str:
        return self._agent.answer(
            request.question, owner=request.owner, l0=request.data_root.l0, now=request.as_of
        )


type ScriptedAnswer = str | Exception


class ScriptedAgent:
    """A fake agent that answers each question id with a scripted answer, or raises its error.

    Every request it receives is recorded in `requests`.
    """

    def __init__(self, answers: Mapping[str, ScriptedAnswer]) -> None:
        self._answers = dict(answers)
        self.requests: list[AgentRequest] = []

    def answer(self, request: AgentRequest) -> str:
        self.requests.append(request)
        scripted = self._answers.get(request.question_id)
        if scripted is None:
            raise AgentError(f"no scripted answer for question {request.question_id}")
        if isinstance(scripted, Exception):
            raise scripted
        return scripted
