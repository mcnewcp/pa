"""The agent runner interface: how the harness asks the agent one question.

A runner answers in free text and cites evidence as `[ep:<episode_id>]`, with what the answer
cost where it knows (dollars, turns, and duration). Anything it raises
(including AgentTimeoutError) is recorded as a failed result for that question; the run goes on.
The harness cannot interrupt a runner, so a runner enforces its own timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pa_core.owner import Owner
from pa_evals.script import Script
from pa_home.agent import AgentAnswer, AgentError, AgentTimeoutError, ClaudeAgent, GraphAccess
from pa_home.agent_project import PA_SERVER
from pa_home.config import DataRoot
from pa_home.mcp_server import graph_server

__all__ = [
    "AgentAnswer",
    "AgentError",
    "AgentRequest",
    "AgentRunner",
    "AgentTimeoutError",
    "ClaudeAgentRunner",
    "GraphAccess",
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
    graph: GraphAccess | None = None
    """The claims graph the agent is given through the PA MCP server; None for files alone."""


class AgentRunner(Protocol):
    def answer(self, request: AgentRequest) -> AgentAnswer:
        """The agent's answer, citing evidence as `[ep:<episode_id>]`."""
        ...


class ClaudeAgentRunner:
    """The real agent (`claude -p` in the agent project), asked at the question's as-of time."""

    def __init__(self, agent: ClaudeAgent) -> None:
        self._agent = agent

    def answer(self, request: AgentRequest) -> AgentAnswer:
        return self._agent.answer(
            request.question,
            owner=request.owner,
            l0=request.data_root.l0,
            now=request.as_of,
            graph=request.graph,
        )


type ScriptedAnswer = str | AgentAnswer | Exception


class ScriptedAgent:
    """A fake agent that answers each question id with a scripted answer, or raises its error.

    A question id may have a list of answers instead, one per run, played in order. An answer
    scripted as text has no cost, turns, or duration.

    Every request it receives is recorded in `requests`, and the MCP tools it was offered for
    each question in `offered`, named as a session names them (`mcp__pa__find`): those the PA
    MCP server lists over the request's graph, less the hidden ones, or none for files alone.
    """

    def __init__(self, answers: Mapping[str, ScriptedAnswer | list[ScriptedAnswer]]) -> None:
        self._answers = Script(answers)
        self.requests: list[AgentRequest] = []
        self.offered: dict[str, frozenset[str]] = {}

    def answer(self, request: AgentRequest) -> AgentAnswer:
        self.requests.append(request)
        self.offered[request.question_id] = _offered(request.graph)
        scripted = self._answers.next(request.question_id)
        if scripted is None:
            raise AgentError(f"no scripted answer for question {request.question_id}")
        if isinstance(scripted, Exception):
            raise scripted
        return AgentAnswer(scripted) if isinstance(scripted, str) else scripted


def _offered(graph: GraphAccess | None) -> frozenset[str]:
    if graph is None:
        return frozenset()
    listed = asyncio.run(graph_server(graph.graph, time_zone=graph.time_zone).list_tools())
    return frozenset(
        f"mcp__{PA_SERVER}__{tool.name}" for tool in listed if tool.name not in graph.hidden_tools
    )
