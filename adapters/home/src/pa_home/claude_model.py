"""A model backend that runs the `claude` CLI in print mode, on the owner's subscription."""

from __future__ import annotations

import json
import tempfile

from pa_core.model_client import ModelBackendError, ModelRequest
from pa_home.claude_cli import ClaudeCliError, run_print


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
        args = [
            "--json-schema",
            json.dumps(request.schema),
            "--no-session-persistence",
            "--tools",
            "",
            "--strict-mcp-config",
        ]
        if self._model:
            args += ["--model", self._model]
        with tempfile.TemporaryDirectory(prefix="pa-claude-") as workdir:
            try:
                result = run_print(
                    self._executable,
                    args,
                    prompt=request.prompt,
                    cwd=workdir,
                    timeout_seconds=self._timeout_seconds,
                )
            except ClaudeCliError as error:
                raise ModelBackendError(str(error)) from error
        if "structured_output" in result:
            return json.dumps(result["structured_output"])
        return str(result.get("result", ""))
