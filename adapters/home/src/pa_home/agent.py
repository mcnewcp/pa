"""The agent: `claude -p` answering the owner's questions from L0, in the agent project."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from string import Template

from pa_core.errors import PaError
from pa_core.owner import Owner

AGENT_PROJECT = Path(__file__).resolve().parents[2] / "agent"
"""`adapters/home/agent/`: the agent project. Its CLAUDE.md is a template (see `ClaudeAgent`)."""

READ_ONLY_TOOLS = "Read,Grep,Glob"
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
    """Answers each question with one `claude -p` session that can only read L0.

    Each question runs in a fresh copy of the agent project in the system temporary directory,
    outside the repository, so no other CLAUDE.md is picked up. The copy's CLAUDE.md is filled
    in with the owner and the L0 path: the project itself names no instance.
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
        """The agent's answer to `question`, asked at `now` (default: the clock's time)."""
        statement = now_statement(now or self._clock())
        prompt = question
        command = [
            self._executable,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--restricted",
            "--tools",
            READ_ONLY_TOOLS,
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--add-dir",
            str(l0),
        ]
        if self._model:
            command += ["--model", self._model]
        system_prompt = ROLE
        if self._as_of_method is AsOfMethod.SYSTEM_PROMPT:
            system_prompt += f"\n\n{statement}"
        elif self._as_of_method is AsOfMethod.PREAMBLE:
            prompt = f"{statement}\n\n{question}"
        command += ["--append-system-prompt", system_prompt]
        with tempfile.TemporaryDirectory(prefix="pa-agent-") as directory:
            workdir = Path(directory) / "project"
            self._copy_project(workdir, owner, l0)
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                raise AgentError(
                    f"Could not run {self._executable!r}: is Claude Code installed and on PATH?"
                ) from error
            except subprocess.TimeoutExpired as error:
                raise AgentTimeoutError(
                    f"the agent did not answer within {self._timeout_seconds:g} seconds"
                ) from error
        return _answer_text(completed)

    def _copy_project(self, workdir: Path, owner: Owner, l0: Path) -> None:
        shutil.copytree(self._project, workdir)
        claude_md = workdir / "CLAUDE.md"
        claude_md.write_text(
            Template(claude_md.read_text()).substitute(
                owner_name=owner.name,
                owner_other_names=", ".join(owner.other_names) or "(none)",
                owner_email_addresses=", ".join(f"`{a}`" for a in owner.email_addresses),
                l0=str(l0),
            )
        )


def _answer_text(completed: subprocess.CompletedProcess[str]) -> str:
    """The answer from `claude -p --output-format json` output, or an AgentError."""
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict) or completed.returncode != 0 or result.get("is_error"):
        detail = (
            (result.get("result") or result.get("subtype")) if isinstance(result, dict) else None
        )
        detail = detail or completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise AgentError(f"claude -p failed (exit {completed.returncode}): {detail}")
    return str(result.get("result", ""))
