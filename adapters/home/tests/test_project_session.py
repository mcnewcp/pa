"""One `claude -p` session in a rendered project, driven through a stand-in `claude` executable.

The project here is not the agent project: it has instructions and settings of its own, as a
diagnostician's does. The stand-in records what the real CLI would see (argv, the prompt on
stdin, its working directory, its environment) and prints what the real CLI prints with
`--output-format stream-json --verbose`: one JSON event per line, the result last.
"""

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from pa_home.claude_cli import ClaudeCliError, ClaudeCliTimeoutError
from pa_home.project_session import ToolCall, run_project_session

REFERRAL = "Why do two questions fail more often on the dreamed wiki?"
INSTRUCTIONS = "You are reading a case. Follow the CLAUDE.md in your working directory."


def _stand_in_claude(tmp_path: Path, body: str = "") -> Path:
    """A `claude` stand-in that saves what it saw to `seen.json`, runs `body`, then replies
    with `reply` after one tool call."""
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, os, sys
            from pathlib import Path
            argv = sys.argv[1:]
            prompt = sys.stdin.read()
            Path({str(tmp_path / "seen.json")!r}).write_text(json.dumps({{
                "argv": argv, "prompt": prompt, "cwd": os.getcwd(), "environ": dict(os.environ),
            }}))
            reply = "The fault is in consolidation."
            """
        )
        + textwrap.dedent(body)
        + textwrap.dedent(
            """\
            events = [
                {"type": "system", "subtype": "init", "session_id": "s1", "tools": []},
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash",
                     "input": {"command": "diff -r own dreamed"}}]}},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1",
                     "content": "Only in own: days", "is_error": False}]}},
                {"type": "result", "subtype": "success", "is_error": False, "result": reply,
                 "total_cost_usd": 2.75, "num_turns": 31, "duration_ms": 412000},
            ]
            for event in events:
                print(json.dumps(event))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "seen.json").read_text())


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """The values following `flag` in `argv`, up to the next flag."""
    start = argv.index(flag) + 1
    values = []
    for value in argv[start:]:
        if value.startswith("--"):
            break
        values.append(value)
    return values


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A rendered project that isn't the agent project."""
    path = tmp_path / "diagnostician"
    (path / ".claude").mkdir(parents=True)
    (path / "CLAUDE.md").write_text("# The diagnostician\n")
    (path / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"additionalDirectories": [str(tmp_path / "case")]}})
    )
    return path


def test_a_session_runs_in_the_given_project_and_its_final_text_comes_back(
    tmp_path: Path, project: Path
):
    reply = run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    assert reply.text == "The fault is in consolidation."
    seen = _seen(tmp_path)
    assert seen["cwd"] == str(project)
    assert seen["argv"][0] == "-p"


def test_the_reply_carries_the_sessions_cost_turns_duration_and_tool_calls(
    tmp_path: Path, project: Path
):
    reply = run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    assert (reply.cost_usd, reply.turns, reply.duration_ms) == (2.75, 31, 412000)
    assert reply.tool_calls == (
        ToolCall(
            "Bash", {"command": "diff -r own dreamed"}, errored=False, output="Only in own: days"
        ),
    )


def test_the_session_is_given_the_projects_settings_as_flag_settings(
    tmp_path: Path, project: Path
):
    run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    argv = _seen(tmp_path)["argv"]
    # claude -p never trusts a folder, so it would drop the project's allow rules and
    # additional directories; passed as flag settings, they hold.
    assert _flag_values(argv, "--settings") == [str(project / ".claude/settings.json")]
    # The permission mode the assistant's sessions run in, with no restriction of its own:
    # what the session may do comes from the settings.
    assert _flag_values(argv, "--permission-mode") == ["auto"]
    for flag in ("--tools", "--allowedTools", "--disallowedTools", "--add-dir"):
        assert flag not in argv


def test_the_session_is_told_only_what_its_caller_says(tmp_path: Path, project: Path):
    run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    seen = _seen(tmp_path)
    # No assistant's role and no as-of statement: the system prompt and the prompt are the
    # caller's, word for word.
    assert _flag_values(seen["argv"], "--append-system-prompt") == [INSTRUCTIONS]
    assert seen["prompt"] == REFERRAL
    assert "--model" not in seen["argv"]


def test_the_session_runs_on_the_model_it_is_given(tmp_path: Path, project: Path):
    run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        model="opus",
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    assert _flag_values(_seen(tmp_path)["argv"], "--model") == ["opus"]


def test_exchange_capture_is_off_whatever_the_callers_environment_says(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
):
    # Capture is on in the caller's environment, as it is in the VM's Remote Control unit.
    monkeypatch.setenv("PA_CAPTURE_EXCHANGES", "1")
    monkeypatch.setenv("PA_HOME_DATA_ROOT", str(tmp_path / "data"))

    run_project_session(
        project,
        prompt=REFERRAL,
        system_prompt=INSTRUCTIONS,
        timeout_seconds=30,
        executable=str(_stand_in_claude(tmp_path)),
    )

    environ = _seen(tmp_path)["environ"]
    assert "PA_CAPTURE_EXCHANGES" not in environ
    # The rest of the caller's environment reaches the session.
    assert environ["PA_HOME_DATA_ROOT"] == str(tmp_path / "data")


def test_a_failed_session_raises_with_the_reason(tmp_path: Path, project: Path):
    claude = _stand_in_claude(
        tmp_path,
        """\
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                          "result": "Usage limit reached"}))
        sys.exit(1)
        """,
    )

    with pytest.raises(ClaudeCliError, match="Usage limit reached"):
        run_project_session(
            project,
            prompt=REFERRAL,
            system_prompt=INSTRUCTIONS,
            timeout_seconds=30,
            executable=str(claude),
        )


def test_a_session_that_runs_too_long_is_stopped_with_a_timeout_error(
    tmp_path: Path, project: Path
):
    claude = _stand_in_claude(tmp_path, "import time; time.sleep(10)\n")

    with pytest.raises(ClaudeCliTimeoutError, match=r"0\.5 seconds"):
        run_project_session(
            project,
            prompt=REFERRAL,
            system_prompt=INSTRUCTIONS,
            timeout_seconds=0.5,
            executable=str(claude),
        )
