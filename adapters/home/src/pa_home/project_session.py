"""One `claude -p` session in a rendered project, whoever's project it is.

The assistant's sessions outside chat run through here (an eval run's and a drafting session's,
in the agent project `pa_home.agent` renders), and so does a session that isn't the
assistant's, in a project of its own. The caller renders the project and says everything the
session is told; nothing here adds a role or a time.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_home.claude_cli import result_number, run_print_stream, without_capture
from pa_home.project import SETTINGS


@dataclass(frozen=True)
class ToolCall:
    """One tool the session called on its way to the answer."""

    tool: str
    """The tool's name, as Claude Code names it (`Read`, `Bash`, `Grep`, ...)."""
    input: Mapping[str, Any]
    """What the tool was called with, as the session passed it."""
    errored: bool = False
    """Whether the tool's result was an error."""
    output: str = ""
    """What the tool returned to the session, as text ("" if it returned none)."""


@dataclass(frozen=True)
class SessionReply:
    """What a session ended with, and what it cost and did, as `claude -p` reports it.

    A figure the CLI didn't report is None.
    """

    text: str
    """The session's final text."""
    cost_usd: float | None
    """The session's cost in US dollars (`total_cost_usd`), at API prices."""
    turns: int | None
    """The session's turns (`num_turns`)."""
    duration_ms: int | None
    """The session's wall-clock time in milliseconds (`duration_ms`)."""
    tool_calls: tuple[ToolCall, ...]
    """The session's tool calls, in the order it made them."""


def run_project_session(
    project: Path,
    *,
    prompt: str,
    system_prompt: str,
    model: str | None = None,
    timeout_seconds: float,
    executable: str = "claude",
) -> SessionReply:
    """Runs one `claude -p` session in the rendered `project`, and returns what it ended with.

    The session's working directory is `project`, and it is told `prompt` and, appended to
    Claude Code's system prompt, `system_prompt`, both as given. It runs on `model` (default:
    the CLI's own) for at most `timeout_seconds`.

    What the session may do comes from the project's settings.json in auto permission mode, as
    in chat, and in the home VM from its managed settings and sandbox too, which every session
    there runs under. Exchange capture is always off: the session's environment drops its
    settings, whatever the caller's environment says. The session keeps no transcript to
    resume from.

    Raises ClaudeCliTimeoutError when the session runs out of time, and ClaudeCliError when
    it cannot be started or fails.
    """
    # Auto, the mode the Claude app starts sessions in, so these sessions run as the owner's do.
    args = ["--no-session-persistence", "--permission-mode", "auto"]
    if model:
        args += ["--model", model]
    args += ["--append-system-prompt", system_prompt]
    # claude -p never trusts a folder, so it drops a project's allow rules and additional
    # directories. Passing the same file as flag settings keeps them.
    args += ["--settings", str(project / SETTINGS)]
    session = run_print_stream(
        executable,
        args,
        prompt=prompt,
        cwd=project,
        timeout_seconds=timeout_seconds,
        # A session run here is never captured as a chat.
        env=without_capture(os.environ),
    )
    result = session.result
    return SessionReply(
        text=str(result.get("result", "")),
        cost_usd=result_number(result, "total_cost_usd", float),
        turns=result_number(result, "num_turns", int),
        duration_ms=result_number(result, "duration_ms", int),
        tool_calls=tool_calls(session.events),
    )


def tool_calls(events: Sequence[Mapping[str, Any]]) -> tuple[ToolCall, ...]:
    """The tool calls in a session's stream-JSON `events`, in the order they were made.

    A call errored when its result says so; a call left without a result did not, and has no
    output.
    """
    calls: list[tuple[str, ToolCall]] = []
    results: dict[str, Mapping[str, Any]] = {}
    for event in events:
        for block in _content(event):
            if block.get("type") == "tool_use":
                name, arguments = block.get("name"), block.get("input")
                calls.append(
                    (
                        str(block.get("id")),
                        ToolCall(
                            str(name),
                            dict(arguments) if isinstance(arguments, Mapping) else {},
                        ),
                    )
                )
            elif block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = block
    return tuple(
        ToolCall(
            call.tool,
            call.input,
            errored=results.get(call_id, {}).get("is_error") is True,
            output=_text(results.get(call_id, {}).get("content")),
        )
        for call_id, call in calls
    )


def _text(content: Any) -> str:
    """A tool result's content as text: a string as it is, or a list's text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
    return ""


def _content(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The content blocks of an assistant or user message event (none for any other event)."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, Mapping)]
