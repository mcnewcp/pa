"""Running the `claude` CLI in print mode with JSON output, on the owner's subscription, and
what every session it runs shares: being told "now", running without exchange capture, and the
figures its result reports."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pa_core.errors import PaError
from pa_core.exchange_capture import CAPTURE_VARS


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


def now_statement(now: datetime) -> str:
    """What a librarian session is told about the current time (see docs/adr/0003).

    The agent is told more, in `pa_home.agent.as_of_statement`: with Sonnet this much alone let
    the real date into some eval answers (#79). The librarian keeps it, since its version
    doesn't cover this text and a change would go unrecorded.

    Raises ValueError if `now` has no UTC offset, since the session is told it with one.
    """
    if now.utcoffset() is None:
        raise ValueError(f"a session's now must have a UTC offset (got {now.isoformat()})")
    offset = now.strftime("%z")
    return (
        f"The current date and time is {now:%A, %B} {now.day}, {now:%Y, %H:%M} "
        f"(UTC{offset[:3]}:{offset[3:]}; {now.isoformat()}). Treat it as now. It overrides "
        "any other date you may see, including today's date in your environment."
    )


def without_capture(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` with exchange capture turned off. Only Remote Control captures exchanges; a
    session run here never does, so its exchanges can't come back into L0 as if the owner had
    said them."""
    return {name: value for name, value in environ.items() if name not in CAPTURE_VARS}


def result_number[N: (int, float)](result: Mapping[str, Any], key: str, kind: type[N]) -> N | None:
    """`result[key]` as a `kind`, or None if it's missing or not a number."""
    value = result.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return kind(value)


def run_print(
    executable: str | Sequence[str],
    args: list[str],
    *,
    prompt: str,
    cwd: Path | str,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The result object of `<executable> -p --output-format json <args>`, prompt on stdin.

    `executable` is the CLI, or a command that runs it (such as one that reaches a VM). It
    runs in `env` (default: this process's environment). Raises ClaudeCliTimeoutError when it
    runs too long, and ClaudeCliError when it cannot be started, exits with an error, or
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
    executable: str | Sequence[str],
    args: list[str],
    prompt: str,
    cwd: Path | str,
    timeout_seconds: float,
    env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Runs the command in a process group of its own, so that when it runs out of time (or
    this process is interrupted) the whole group is killed, every process it started included,
    before this returns: nothing it started can write on after its caller has moved on."""
    command = [executable] if isinstance(executable, str) else list(executable)
    try:
        process = subprocess.Popen(
            [*command, "-p", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise ClaudeCliError(
            f"Could not run {command[0]!r}: is Claude Code installed and on PATH?"
        ) from error
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        _kill_group(process)
        raise ClaudeCliTimeoutError(
            f"claude -p did not answer within {timeout_seconds:g} seconds"
        ) from error
    except BaseException:
        _kill_group(process)
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kills `process` and every process in its group, and waits for it."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()


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
