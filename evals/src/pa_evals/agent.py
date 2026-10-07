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

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_home.config import DataRoot


class AgentError(PaError):
    """The agent could not answer a question."""


class AgentTimeoutError(AgentError):
    """The agent took longer than its runner allows."""


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
