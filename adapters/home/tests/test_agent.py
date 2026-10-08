"""The agent: `claude -p` in the agent project, driven through a stand-in `claude` executable.

The stand-in records what the real CLI would see (argv, the prompt on stdin, the project's
CLAUDE.md in its working directory) and prints a result shaped like
`claude -p --output-format json`. The real CLI is exercised only by the opt-in smoke tests.
"""

import json
import os
import stat
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pa_core.owner import Owner
from pa_home.agent import AgentError, AgentTimeoutError, AsOfMethod, ClaudeAgent

SATURDAY_MORNING = datetime(2026, 10, 10, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))


def _fake_claude(tmp_path: Path, body: str = "") -> Path:
    """A `claude` stand-in that saves what it saw to `seen.json`, then runs `body`.

    It replies with `reply` (default: an answer citing one episode) unless `body` exits first.
    """
    script = tmp_path / "claude"
    seen = tmp_path / "seen.json"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, os, sys
            from pathlib import Path
            argv = sys.argv[1:]
            prompt = sys.stdin.read()
            claude_md = Path("CLAUDE.md")
            settings = Path(".claude/settings.json")
            settings = json.loads(settings.read_text()) if settings.exists() else None
            Path({str(seen)!r}).write_text(json.dumps({{
                "argv": argv,
                "prompt": prompt,
                "cwd": os.getcwd(),
                "claude_md": claude_md.read_text() if claude_md.exists() else None,
                "settings": settings,
                "scratch_is_a_directory": bool(settings) and Path(
                    settings["sandbox"]["filesystem"]["allowWrite"][0]
                ).is_dir(),
            }}))
            reply = "The hotel block closes on Oct 9 [ep:gmail_94f6b4cbc55a3b85]."
            """
        )
        + textwrap.dedent(body)
        + textwrap.dedent(
            """\
            print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": reply}))
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
def l0(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "l0"
    path.mkdir(parents=True)
    return path


def test_the_agents_answer_is_returned(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))

    answer = agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)

    assert answer == "The hotel block closes on Oct 9 [ep:gmail_94f6b4cbc55a3b85]."


def test_the_agent_runs_under_the_rendered_projects_settings(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))

    agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)

    seen = _seen(tmp_path)
    argv = seen["argv"]
    assert argv[0] == "-p"
    # claude -p never trusts a folder, so it would drop a project's allow rules and additional
    # directories; the same settings file is passed as flag settings, which it doesn't drop.
    assert _flag_values(argv, "--settings") == [str(Path(seen["cwd"]) / ".claude/settings.json")]
    assert seen["settings"]["permissions"]["additionalDirectories"][0] == str(l0)
    assert seen["scratch_is_a_directory"]
    # Permissions come from settings.json alone, as in chat: no command-line restrictions.
    for flag in (
        "--tools",
        "--allowedTools",
        "--disallowedTools",
        "--restricted",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--add-dir",
        "--permission-mode",
        "--dangerously-skip-permissions",
    ):
        assert flag not in argv


def test_the_agent_runs_in_the_agent_project_told_who_the_owner_is_and_where_l0_is(
    tmp_path: Path, l0: Path
):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))

    agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)

    claude_md = _seen(tmp_path)["claude_md"]
    for value in ("Argus McNevans", "Gus", "argus@example.com", str(l0), "[ep:<episode_id>]"):
        assert value in claude_md
    assert "$" not in claude_md, "a placeholder was left unfilled"
    # Claude Code's own system prompt casts it as a coding assistant; it is told otherwise.
    [system_prompt] = _flag_values(_seen(tmp_path)["argv"], "--append-system-prompt")
    assert "personal assistant" in system_prompt and "CLAUDE.md" in system_prompt


def test_the_agent_project_names_no_owner_of_its_own(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))
    other = Owner(name="Wren Okafor", email_addresses=("wren@example.org",))

    agent.answer("What's on today?", owner=other, l0=l0)

    claude_md = _seen(tmp_path)["claude_md"]
    assert "Wren Okafor" in claude_md and "wren@example.org" in claude_md
    assert "Argus" not in claude_md and "argus@" not in claude_md


def test_the_agent_runs_outside_the_repository_so_no_other_instructions_are_picked_up(
    tmp_path: Path, l0: Path
):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))

    agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)

    cwd = Path(_seen(tmp_path)["cwd"])
    repository = Path(__file__).resolve().parents[3]
    assert not cwd.is_relative_to(repository)
    assert not cwd.exists(), "the per-question project copy is cleaned up"
    scratch = Path(_seen(tmp_path)["settings"]["sandbox"]["filesystem"]["allowWrite"][0])
    assert not scratch.is_relative_to(repository)
    assert not scratch.exists(), "the per-question scratch directory is cleaned up"


def test_now_is_appended_to_the_system_prompt(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(
        executable=str(_fake_claude(tmp_path)), as_of_method=AsOfMethod.SYSTEM_PROMPT
    )

    agent.answer("What day of the week is it?", owner=OWNER, l0=l0, now=SATURDAY_MORNING)

    seen = _seen(tmp_path)
    [system_prompt] = _flag_values(seen["argv"], "--append-system-prompt")
    assert "Saturday, October 10, 2026, 09:00" in system_prompt
    assert "2026-10-10T09:00:00-05:00" in system_prompt
    assert seen["prompt"] == "What day of the week is it?"


def test_now_can_be_stated_in_a_message_before_the_question(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)), as_of_method=AsOfMethod.PREAMBLE)

    agent.answer("What day of the week is it?", owner=OWNER, l0=l0, now=SATURDAY_MORNING)

    seen = _seen(tmp_path)
    [system_prompt] = _flag_values(seen["argv"], "--append-system-prompt")
    assert "2026" not in system_prompt
    preamble, question = seen["prompt"].rsplit("\n\n", 1)
    assert "Saturday, October 10, 2026, 09:00" in preamble
    assert question == "What day of the week is it?"


def test_without_an_injected_now_the_agent_tells_the_time_by_its_clock(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(
        executable=str(_fake_claude(tmp_path)),
        as_of_method=AsOfMethod.PREAMBLE,
        clock=lambda: SATURDAY_MORNING,
    )

    agent.answer("What day of the week is it?", owner=OWNER, l0=l0)

    assert "Saturday, October 10, 2026, 09:00" in _seen(tmp_path)["prompt"]


def test_the_control_method_injects_no_time_at_all(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)), as_of_method=AsOfMethod.NONE)

    agent.answer("What day of the week is it?", owner=OWNER, l0=l0, now=SATURDAY_MORNING)

    seen = _seen(tmp_path)
    [system_prompt] = _flag_values(seen["argv"], "--append-system-prompt")
    assert "2026" not in system_prompt
    assert seen["prompt"] == "What day of the week is it?"


def test_a_now_without_a_utc_offset_is_refused_before_the_agent_runs(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)))

    with pytest.raises(ValueError, match="UTC offset"):
        agent.answer(
            "What day of the week is it?", owner=OWNER, l0=l0, now=datetime(2026, 10, 10, 9, 0)
        )

    assert not (tmp_path / "seen.json").exists()


def test_a_clock_without_a_utc_offset_is_refused_too(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(
        executable=str(_fake_claude(tmp_path)), clock=lambda: datetime(2026, 10, 10, 9, 0)
    )

    with pytest.raises(ValueError, match="UTC offset"):
        agent.answer("What day of the week is it?", owner=OWNER, l0=l0)


def test_the_agent_runs_on_the_chosen_model(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(_fake_claude(tmp_path)), model="sonnet")

    agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)

    assert _flag_values(_seen(tmp_path)["argv"], "--model") == ["sonnet"]


def test_a_failed_claude_session_is_an_agent_error_with_the_reason(tmp_path: Path, l0: Path):
    claude = _fake_claude(
        tmp_path,
        """\
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                          "result": "Usage limit reached"}))
        sys.exit(1)
        """,
    )
    agent = ClaudeAgent(executable=str(claude))

    with pytest.raises(AgentError, match="Usage limit reached"):
        agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)


def test_an_agent_that_runs_too_long_is_stopped_with_a_timeout_error(tmp_path: Path, l0: Path):
    claude = _fake_claude(tmp_path, "import time; time.sleep(10)\n")
    agent = ClaudeAgent(executable=str(claude), timeout_seconds=0.5)

    with pytest.raises(AgentTimeoutError, match=r"0\.5 seconds"):
        agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)


TWO_EXCHANGES = (
    Path(__file__).parents[3] / "core/tests/fixtures/claude_code_transcripts/two_exchanges.jsonl"
)
INSTALL_REPLY = (
    "L0 doesn't say when the countertop will be installed. Birchwood's last email only "
    "confirms the order [ep:gmail_0123456789abcdef]."
)


def test_an_agent_run_captures_no_exchange_even_where_capture_is_on(
    tmp_path: Path, l0: Path, monkeypatch: pytest.MonkeyPatch
):
    """Eval runs are never captured, even from a shell where the chat service's settings leak."""
    inbox = tmp_path / "data" / "inbox" / "assistant_chat"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("PA_CAPTURE_EXCHANGES", "1")
    monkeypatch.setenv("PA_CAPTURE_INBOX", str(inbox))
    hook_statuses = tmp_path / "hook_statuses.json"
    # The stand-in stops the way Claude Code does: it fires the project's Stop hooks.
    claude = _fake_claude(
        tmp_path,
        f"""\
        import subprocess
        stop = {{"session_id": "3f0c9a7e-5b21-4d8e-a6f3-91c2e7d40b58",
                 "transcript_path": {str(TWO_EXCHANGES)!r}, "hook_event_name": "Stop",
                 "stop_hook_active": False, "last_assistant_message": {INSTALL_REPLY!r}}}
        statuses = [
            subprocess.run([hook["command"], *hook["args"]], input=json.dumps(stop), text=True,
                           env={{**os.environ, "CLAUDE_PROJECT_DIR": os.getcwd()}}).returncode
            for group in settings["hooks"]["Stop"] for hook in group["hooks"]
        ]
        Path({str(hook_statuses)!r}).write_text(json.dumps(statuses))
        """,
    )
    agent = ClaudeAgent(executable=str(claude))

    agent.answer("When are they installing the countertop?", owner=OWNER, l0=l0)

    assert json.loads(hook_statuses.read_text()) == [0]
    assert list(inbox.iterdir()) == []


def test_a_missing_claude_executable_is_an_agent_error(tmp_path: Path, l0: Path):
    agent = ClaudeAgent(executable=str(tmp_path / "no-such-claude"))

    with pytest.raises(AgentError, match="installed"):
        agent.answer("When does the hotel block close?", owner=OWNER, l0=l0)


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_the_real_agent_cannot_write_files(tmp_path: Path, l0: Path):
    episode = l0 / "episodes" / "gmail" / "2026-10" / "gmail_94f6b4cbc55a3b85.json"
    episode.parent.mkdir(parents=True)
    episode.write_text('{"body": "The hotel block closes on Oct 9."}')
    agent = ClaudeAgent(model=os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku"))

    agent.answer(
        f"Please change {episode} so its body says Oct 10 instead of Oct 9, then create a "
        f"file named {l0 / 'todo.txt'} that says 'book hotel'. Use any tool you have.",
        owner=OWNER,
        l0=l0,
    )

    assert episode.read_text() == '{"body": "The hotel block closes on Oct 9."}'
    assert [p.name for p in l0.rglob("*") if p.is_file()] == [episode.name]
