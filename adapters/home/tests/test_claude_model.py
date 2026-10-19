"""The `claude -p` model backend, driven through a stand-in `claude` executable.

The stand-in reads the request the way the real CLI does (prompt on stdin, flags in argv) and
prints a result shaped like `claude -p --output-format json`. The real CLI is exercised only by
the opt-in smoke test at the end.
"""

import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import BaseModel

from pa_core.model_client import ModelBackendError, ModelClient
from pa_home.claude_model import ClaudeCliBackend


class Echo(BaseModel):
    prompt: str
    schema_title: str


def _fake_claude(tmp_path: Path, body: str) -> Path:
    """A `claude` stand-in; `body` runs with `args` (argv after flags) and `prompt` (stdin)."""
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json, sys
            argv = sys.argv[1:]
            args = dict(zip(argv, argv[1:]))
            prompt = sys.stdin.read()
            """
        )
        + textwrap.dedent(body)
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_the_structured_output_of_claude_print_mode_is_returned(tmp_path: Path):
    claude = _fake_claude(
        tmp_path,
        """\
        assert "-p" in argv and args["--output-format"] == "json"
        schema = json.loads(args["--json-schema"])
        print(json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": {"prompt": prompt, "schema_title": schema["title"]},
        }))
        """,
    )
    client = ModelClient(ClaudeCliBackend(executable=str(claude)))

    echo = client.generate("Say hello to Argus.", Echo)

    assert echo == Echo(prompt="Say hello to Argus.", schema_title="Echo")


def test_a_failed_claude_call_is_a_backend_error_with_the_reason(tmp_path: Path):
    claude = _fake_claude(
        tmp_path,
        """\
        print(json.dumps({
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "Usage limit reached",
        }))
        sys.exit(1)
        """,
    )
    client = ModelClient(ClaudeCliBackend(executable=str(claude)))

    with pytest.raises(ModelBackendError, match="Usage limit reached"):
        client.generate("Say hello to Argus.", Echo)


def test_a_missing_claude_executable_is_a_backend_error(tmp_path: Path):
    client = ModelClient(ClaudeCliBackend(executable=str(tmp_path / "no-such-claude")))

    with pytest.raises(ModelBackendError, match="installed"):
        client.generate("Say hello to Argus.", Echo)


class Weekday(BaseModel):
    day: str
    day_number: int


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_a_tiny_schema_round_trips_through_the_real_claude_cli():
    client = ModelClient(ClaudeCliBackend(model=os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku")))

    weekday = client.generate(
        "Which day of the week is 2026-10-03? Give its English name in `day` and its ISO "
        "weekday number (Monday is 1) in `day_number`.",
        Weekday,
    )

    assert weekday == Weekday(day="Saturday", day_number=6)
