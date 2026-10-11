"""The agent: `claude -p` answering the owner's questions from L0, in the agent project."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pa_core.errors import PaError
from pa_core.exchange_capture import CAPTURE_VARS
from pa_core.owner import Owner
from pa_home.agent_project import AGENT_PROJECT, render_agent_project
from pa_home.claude_cli import ClaudeCliError, ClaudeCliTimeoutError, run_print

DEFAULT_TIMEOUT_SECONDS = 600

ROLE = (
    "In this session you are not a coding assistant. You are a personal assistant, and the "
    "CLAUDE.md in your working directory says whose and how to answer: follow it. Every "
    "question is about your owner's life, and the answer is in L0, so search L0 before you "
    "answer."
)
"""Appended to Claude Code's system prompt, which otherwise casts the agent as a coding assistant
that declines personal questions without looking (see docs/adr/0003)."""


class AgentError(PaError):
    """The agent could not answer a question."""


class AgentTimeoutError(AgentError):
    """The agent took longer than it is allowed."""


class AsOfMethod(StrEnum):
    """How the agent is told what "now" is. See docs/adr/0003 for why the default won."""

    SYSTEM_PROMPT = "system-prompt"
    """Appended to Claude Code's system prompt."""
    PREAMBLE = "preamble"
    """Stated in the message, before the question."""
    NONE = "none"
    """Not told at all (the control: the agent falls back to the real date)."""


DEFAULT_AS_OF_METHOD = AsOfMethod.SYSTEM_PROMPT


def wall_clock() -> datetime:
    """The current local time, with its UTC offset."""
    return datetime.now().astimezone()


def now_statement(now: datetime) -> str:
    """What the agent is told about the current time."""
    offset = now.strftime("%z")
    return (
        f"The current date and time is {now:%A, %B} {now.day}, {now:%Y, %H:%M} "
        f"(UTC{offset[:3]}:{offset[3:]}; {now.isoformat()}). Treat it as now. It overrides "
        "any other date you may see, including today's date in your environment."
    )


class ClaudeAgent:
    """Answers each question with one `claude -p` session in a rendered agent project.

    Each question runs in a fresh rendering of the agent project (see `render_agent_project`)
    in the system temporary directory, outside the repository, so no other CLAUDE.md is picked
    up, with a fresh scratch directory beside it. What the session may do comes from the
    project's settings.json in auto permission mode, as in chat. Exchange capture is always
    off: the session's environment drops its settings, whatever the caller's environment says.
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

    def answer(self, question: str, *, owner: Owner, l0: Path, now: datetime | None = None) -> str:
        """The agent's answer to `question`, asked at `now` (default: the clock's time).

        Raises ValueError if that time has no UTC offset, since the agent is told it with one.
        """
        now = now or self._clock()
        if now.utcoffset() is None:
            raise ValueError(f"the agent's now must have a UTC offset (got {now.isoformat()})")
        statement = now_statement(now)
        prompt = question
        # Auto, the mode the Claude app starts sessions in, so eval sessions run as the owner's do.
        args = ["--no-session-persistence", "--permission-mode", "auto"]
        if self._model:
            args += ["--model", self._model]
        system_prompt = ROLE
        if self._as_of_method is AsOfMethod.SYSTEM_PROMPT:
            system_prompt += f"\n\n{statement}"
        elif self._as_of_method is AsOfMethod.PREAMBLE:
            prompt = f"{statement}\n\n{question}"
        args += ["--append-system-prompt", system_prompt]
        with tempfile.TemporaryDirectory(prefix="pa-agent-") as directory:
            workdir = Path(directory) / "project"
            scratch = Path(directory) / "scratch"
            scratch.mkdir()
            render_agent_project(workdir, owner=owner, l0=l0, scratch=scratch, source=self._project)
            # claude -p never trusts a folder, so it drops a project's allow rules and
            # additional directories. Passing the same file as flag settings keeps them.
            args += ["--settings", str(workdir / ".claude" / "settings.json")]
            try:
                result = run_print(
                    self._executable,
                    args,
                    prompt=prompt,
                    cwd=workdir,
                    timeout_seconds=self._timeout_seconds,
                    env=_without_capture(os.environ),
                )
            except ClaudeCliTimeoutError as error:
                raise AgentTimeoutError(str(error)) from error
            except ClaudeCliError as error:
                raise AgentError(str(error)) from error
        return str(result.get("result", ""))


def _without_capture(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` with exchange capture turned off: an eval session's exchanges are never
    captured, so the assistant's answers can't come back into L0 as if the owner had said them."""
    return {name: value for name, value in environ.items() if name not in CAPTURE_VARS}
