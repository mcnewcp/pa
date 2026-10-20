"""Running the `claude` CLI in print mode with JSON output, on the owner's subscription."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_core.errors import PaError


class ClaudeCliError(PaError):
    """A `claude -p` call that could not run or did not succeed."""


class ClaudeCliTimeoutError(ClaudeCliError):
    """A `claude -p` call that took longer than it is allowed."""


@dataclass(frozen=True)
class StreamedPrint:
    """What `claude -p --output-format stream-json` printed: its result and every event."""

    result: dict[str, Any]
    """The final `result` event, shaped as `--output-format json` prints it alone."""
    events: list[dict[str, Any]]
    """Every event in the order printed, the result last."""


def run_print(
    executable: str,
    args: list[str],
    *,
    prompt: str,
    cwd: Path | str,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The result object of `<executable> -p --output-format json <args>`, prompt on stdin.

    It runs in `env` (default: this process's environment). Raises ClaudeCliTimeoutError when
    it runs too long, and ClaudeCliError when it cannot be started, exits with an error, or
    reports one.
    """
    completed = _run(
        executable, ["--output-format", "json", *args], prompt, cwd, timeout_seconds, env
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    return _checked(completed, result)


def run_print_stream(
    executable: str,
    args: list[str],
    *,
    prompt: str,
    cwd: Path | str,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> StreamedPrint:
    """The events of `<executable> -p --output-format stream-json --verbose <args>`.

    As `run_print`, but the session's every event (its messages, tool calls, and tool results)
    comes back beside the result. A line that isn't a JSON object is skipped.
    """
    completed = _run(
        executable,
        ["--output-format", "stream-json", "--verbose", *args],
        prompt,
        cwd,
        timeout_seconds,
        env,
    )
    events = [event for line in completed.stdout.splitlines() if (event := _event(line))]
    results = [event for event in events if event.get("type") == "result"]
    return StreamedPrint(_checked(completed, results[-1] if results else None), events)


def _run(
    executable: str,
    args: list[str],
    prompt: str,
    cwd: Path | str,
    timeout_seconds: float,
    env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [executable, "-p", *args],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as error:
        raise ClaudeCliError(
            f"Could not run {executable!r}: is Claude Code installed and on PATH?"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ClaudeCliTimeoutError(
            f"claude -p did not answer within {timeout_seconds:g} seconds"
        ) from error


def _event(line: str) -> dict[str, Any] | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _checked(completed: subprocess.CompletedProcess[str], result: Any) -> dict[str, Any]:
    """`result`, unless it's missing or the run failed."""
    if not isinstance(result, dict) or completed.returncode != 0 or result.get("is_error"):
        detail = (
            (result.get("result") or result.get("subtype")) if isinstance(result, dict) else None
        )
        detail = detail or completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ClaudeCliError(f"claude -p failed (exit {completed.returncode}): {detail}")
    return result
