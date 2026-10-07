"""Running the `claude` CLI in print mode with JSON output, on the owner's subscription."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pa_core.errors import PaError


class ClaudeCliError(PaError):
    """A `claude -p` call that could not run or did not succeed."""


class ClaudeCliTimeoutError(ClaudeCliError):
    """A `claude -p` call that took longer than it is allowed."""


def run_print(
    executable: str, args: list[str], *, prompt: str, cwd: Path | str, timeout_seconds: float
) -> dict[str, Any]:
    """The result object of `<executable> -p --output-format json <args>`, prompt on stdin.

    Raises ClaudeCliTimeoutError when it runs too long, and ClaudeCliError when it cannot be
    started, exits with an error, or reports one.
    """
    command = [executable, "-p", "--output-format", "json", *args]
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
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
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict) or completed.returncode != 0 or result.get("is_error"):
        detail = (
            (result.get("result") or result.get("subtype")) if isinstance(result, dict) else None
        )
        detail = detail or completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ClaudeCliError(f"claude -p failed (exit {completed.returncode}): {detail}")
    return result
