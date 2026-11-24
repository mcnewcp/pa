"""The agent: `claude -p` answering the owner's questions from L0, in the agent project."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_home.agent_project import AGENT_PROJECT, render_agent_project
from pa_home.claude_cli import ClaudeCliError, ClaudeCliTimeoutError
from pa_home.project_session import ToolCall as ToolCall  # an answer's tool calls are these
from pa_home.project_session import run_project_session

DEFAULT_TIMEOUT_SECONDS = 600

ROLE = (
    "In this session you are not a coding assistant. You are a personal assistant, and the "
    "CLAUDE.md in your working directory says whose and how to answer: follow it. Every "
    "question is about your owner's life, and the answer is in what has been captured for them, "
    "so look it up before you answer."
)
"""Appended to Claude Code's system prompt, which otherwise casts the agent as a coding assistant
that declines personal questions without looking (see docs/adr/0003).

It says to look the answer up but not where, so CLAUDE.md decides. Until v0.12 it ended "the
answer is in L0, so search L0 before you answer"; runs before and after the change are not
strictly comparable."""


class AgentError(PaError):
    """The agent could not answer a question."""


class AgentTimeoutError(AgentError):
    """The agent took longer than it is allowed."""


@dataclass(frozen=True)
class AgentAnswer:
    """The agent's answer, with what the session cost and did, as `claude -p` reports it.

    A figure the CLI didn't report is None.
    """

    text: str
    cost_usd: float | None = None
    """The session's cost in US dollars (`total_cost_usd`), at API prices."""
    turns: int | None = None
    """The session's turns (`num_turns`)."""
    duration_ms: int | None = None
    """The session's wall-clock time in milliseconds (`duration_ms`)."""
    tool_calls: tuple[ToolCall, ...] | None = None
    """The session's tool calls, in the order it made them. None where they weren't recorded
    (an answer that didn't come from a session, such as a scripted one)."""


class AsOfMethod(StrEnum):
    """How the agent is told what "now" is. See docs/adr/0003 for why the default won."""

    SYSTEM_PROMPT = "system-prompt"
    """Appended to Claude Code's system prompt."""
    PREAMBLE = "preamble"
    """Stated in the message, before the question."""
    BOTH = "both"
    """Appended to the system prompt, and stated before the question too."""
    NONE = "none"
    """Not told at all (the control: the agent falls back to the real date)."""


DEFAULT_AS_OF_METHOD = AsOfMethod.BOTH


def as_of_statement(now: datetime) -> str:
    """What the agent is told about the current time (see docs/adr/0003).

    Claude Code also states the machine's date in every session's context ("Today's date is
    <date>."), which can't be turned off, so the statement says outright that it isn't now and
    names the date that date arithmetic starts from.

    Raises ValueError if `now` has no UTC offset, since the agent is told it with one.
    """
    if now.utcoffset() is None:
        raise ValueError(f"a session's now must have a UTC offset (got {now.isoformat()})")
    offset = now.strftime("%z")
    day = f"{now:%A, %B} {now.day}, {now:%Y}"
    return (
        f"It is now {day}, {now:%H:%M} (UTC{offset[:3]}:{offset[3:]}; {now.isoformat()}). "
        "That is the current date and time for this whole conversation. Claude Code also states "
        "a date in your context (\"Today's date is ...\"): that is the machine's clock, not now, "
        "so don't use it or mention it. Work out today, tomorrow, this week, how many days are "
        "left, and whether anything is overdue, has passed or has happened yet from "
        f"{day} alone."
    )


def wall_clock() -> datetime:
    """The current local time, with its UTC offset."""
    return datetime.now().astimezone()


class ClaudeAgent:
    """Answers each question with one `claude -p` session in a rendered agent project.

    Each question runs in a fresh rendering of the agent project (see `render_agent_project`)
    in the system temporary directory, outside the repository, so no other CLAUDE.md is picked
    up, with a fresh scratch directory beside it. The session is told the assistant's role and
    what "now" is, and runs as `run_project_session` runs any session: under the project's
    settings in auto permission mode, as in chat, with exchange capture always off.
    """

    def __init__(
        self,
        *,
        project: Path = AGENT_PROJECT,
        model: str | None = None,
        executable: str = "claude",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        as_of_method: AsOfMethod = DEFAULT_AS_OF_METHOD,
        clock: Callable[[], datetime] = wall_clock,
    ) -> None:
        self._project = project
        self._model = model
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._as_of_method = as_of_method
        self._clock = clock

    def answer(
        self,
        question: str,
        *,
        owner: Owner,
        l0: Path,
        wiki: Path | None = None,
        status: Path | None = None,
        now: datetime | None = None,
    ) -> AgentAnswer:
        """The agent's answer to `question`, asked at `now` (default: the clock's time).

        Given a `wiki`, the agent reads it beside `l0` and is told how to find its way with it;
        without one, it reads L0 alone. Given a `status` file, it reads it too, and is told to
        scope what it says by how current each source is.

        Raises ValueError if that time has no UTC offset, since the agent is told it with one.
        """
        now = now or self._clock()
        statement = as_of_statement(now)
        prompt = question
        system_prompt = ROLE
        if self._as_of_method in (AsOfMethod.SYSTEM_PROMPT, AsOfMethod.BOTH):
            system_prompt += f"\n\n{statement}"
        if self._as_of_method in (AsOfMethod.PREAMBLE, AsOfMethod.BOTH):
            prompt = f"{statement}\n\n{question}"
        with tempfile.TemporaryDirectory(prefix="pa-agent-") as directory:
            workdir = Path(directory) / "project"
            scratch = Path(directory) / "scratch"
            scratch.mkdir()
            render_agent_project(
                workdir,
                owner=owner,
                l0=l0,
                scratch=scratch,
                wiki=wiki,
                status=status,
                source=self._project,
            )
            try:
                reply = run_project_session(
                    workdir,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=self._model,
                    timeout_seconds=self._timeout_seconds,
                    executable=self._executable,
                )
            except ClaudeCliTimeoutError as error:
                raise AgentTimeoutError(str(error)) from error
            except ClaudeCliError as error:
                raise AgentError(str(error)) from error
        return AgentAnswer(
            text=reply.text,
            cost_usd=reply.cost_usd,
            turns=reply.turns,
            duration_ms=reply.duration_ms,
            tool_calls=reply.tool_calls,
        )
