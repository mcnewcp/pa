"""The home session runner: the librarian's sessions as `claude -p` in the rendered project.

Each session is started with only the file tools (`--tools`), the rendered project's settings
as flag settings (which attach L0 and the wiki), "now" appended to the system prompt
(ADR-0003), JSON output, a turn cap, and a timeout. The command that runs `claude` is
configuration: in production it reaches the librarian's VM, and tests point it at a stand-in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from pa_core.librarian.run import Phase, SessionPaths, SessionResult
from pa_home.claude_cli import (
    ClaudeCliError,
    ClaudeCliTimeoutError,
    now_statement,
    result_number,
    run_print,
    without_capture,
)
from pa_home.config import LIBRARIAN_TIMEOUT_VAR
from pa_home.project import SETTINGS

FILE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")
"""The only tools a librarian session is started with."""

ROLE = (
    "In this session you are not a coding assistant. You are the librarian of a personal "
    "assistant, and the CLAUDE.md in your working directory says how to keep the wiki: follow "
    "it."
)
"""Appended to Claude Code's system prompt, before "now"."""


class ClaudeSessionRunner:
    """Runs librarian sessions with `<command> -p` in a rendered librarian project.

    `project` is the rendered project, the sessions' working directory; `paths` are where the
    sessions see L0 and the wiki (the paths the project was rendered with). Sessions keep their
    transcripts, so a session can be resumed from the same project.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        project: Path,
        paths: SessionPaths,
        model: str,
        effort: str,
        max_turns: int,
        timeout_seconds: float,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self._command = list(command)
        self._project = project
        self._paths = paths
        self._model = model
        self._effort = effort
        self._max_turns = max_turns
        self._timeout_seconds = timeout_seconds
        self._environ = environ
        self._phases: dict[str, Phase] = {}
        """The phase of each session started, by its id, for when it's resumed."""

    @property
    def paths(self) -> SessionPaths:
        return self._paths

    def start(self, phase: Phase, prompt: str, *, now: datetime) -> SessionResult:
        result = self._run(phase, prompt, now, [])
        if result.session_id is not None:
            self._phases[result.session_id] = phase
        return result

    def resume(self, session_id: str, prompt: str, *, now: datetime) -> SessionResult:
        return self._run(self._phases.get(session_id), prompt, now, ["--resume", session_id])

    def _run(
        self, phase: Phase | None, prompt: str, now: datetime, extra: list[str]
    ) -> SessionResult:
        args = [
            *extra,
            "--tools",
            ",".join(FILE_TOOLS),
            "--settings",
            str(self._project / SETTINGS),
            # Edits must not wait for approval in print mode; the mounts are the fence.
            "--permission-mode",
            "acceptEdits",
            "--model",
            self._model,
            "--effort",
            self._effort,
            "--max-turns",
            str(self._max_turns),
            "--append-system-prompt",
            f"{ROLE}\n\n{now_statement(now)}",
        ]
        try:
            result = run_print(
                self._command,
                args,
                prompt=prompt,
                cwd=self._project,
                timeout_seconds=self._timeout_seconds,
                # A librarian session is never captured as a chat.
                env=without_capture(self._environ),
            )
        except ClaudeCliTimeoutError as error:
            session = f"the {phase} session" if phase else "a session"
            return SessionResult(
                ok=False,
                error=f"{error}; if {session} needs longer, raise {LIBRARIAN_TIMEOUT_VAR}",
            )
        except ClaudeCliError as error:
            return SessionResult(ok=False, error=str(error))
        session_id = result.get("session_id")
        return SessionResult(
            ok=True,
            session_id=session_id if isinstance(session_id, str) else None,
            cost_usd=result_number(result, "total_cost_usd", float),
            turns=result_number(result, "num_turns", int),
        )
