"""A model backend that runs the `claude` CLI in print mode, on the owner's subscription."""

from __future__ import annotations

import json
import subprocess
import tempfile

from pa_core.model_client import ModelBackendError, ModelRequest


class ClaudeCliBackend:
    """Answers each request with one `claude -p --output-format json --json-schema` call.

    The prompt goes in on stdin. The call runs with no tools and no MCP servers, without saving
    a session, in an empty temporary directory so no project's CLAUDE.md is picked up.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        executable: str = "claude",
        timeout_seconds: float = 300,
    ) -> None:
        self._model = model
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> str:
        command = [
            self._executable,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(request.schema),
            "--no-session-persistence",
            "--tools",
            "",
            "--strict-mcp-config",
        ]
        if self._model:
            command += ["--model", self._model]
        with tempfile.TemporaryDirectory(prefix="pa-claude-") as workdir:
            try:
                completed = subprocess.run(
                    command,
                    input=request.prompt,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                raise ModelBackendError(
                    f"Could not run {self._executable!r}: is Claude Code installed and on PATH?"
                ) from error
            except subprocess.TimeoutExpired as error:
                raise ModelBackendError(
                    f"claude -p did not answer within {self._timeout_seconds:g} seconds."
                ) from error
        return _reply_text(completed)


def _reply_text(completed: subprocess.CompletedProcess[str]) -> str:
    """The model's reply from `claude -p --output-format json` output, or a ModelBackendError."""
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = None
    if not isinstance(result, dict) or completed.returncode != 0 or result.get("is_error"):
        detail = (
            (result.get("result") or result.get("subtype")) if isinstance(result, dict) else None
        )
        detail = detail or completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ModelBackendError(f"claude -p failed (exit {completed.returncode}): {detail}")
    if "structured_output" in result:
        return json.dumps(result["structured_output"])
    return str(result.get("result", ""))
