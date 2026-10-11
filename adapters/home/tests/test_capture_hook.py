"""The exchange capture hook, fired from a rendered agent project the way Claude Code fires it.

Claude Code runs each `Stop` hook in the project's settings.json when the assistant finishes a
reply: the command with its arguments (no shell), the hook input as JSON on stdin, and its own
environment plus CLAUDE_PROJECT_DIR. These tests do the same with transcripts recorded from
Claude Code 2.1.x (see core/tests/test_exchange_capture.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from pa_core.exchange_capture import run_stop_hook
from pa_core.owner import Owner
from pa_home.agent_project import render_agent_project
from pa_home.cli import main

TRANSCRIPTS = Path(__file__).parents[3] / "core" / "tests" / "fixtures" / "claude_code_transcripts"
TWO_EXCHANGES = (TRANSCRIPTS / "two_exchanges.jsonl").read_text()
SESSION_ID = "3f0c9a7e-5b21-4d8e-a6f3-91c2e7d40b58"
QUOTE_REPLY = (
    "Rick quoted $5,100 for quartz counters, installed [ep:gmail_fedcba9876543210].\n\n"
    "You went with Birchwood's quote of $4,200 instead [ep:gmail_0123456789abcdef]."
)
INSTALL_REPLY = (
    "L0 doesn't say when the countertop will be installed. Birchwood's last email only "
    "confirms the order [ep:gmail_0123456789abcdef]."
)
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
CAPTURE_VARS = ("PA_CAPTURE_EXCHANGES", "PA_CAPTURE_INBOX")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "rendered"
    render_agent_project(target, owner=OWNER, l0=tmp_path / "l0", scratch=tmp_path / "scratch")
    return target


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "inbox" / "assistant_chat"
    path.mkdir(parents=True)
    return path


class Session:
    """A session's transcript file, written up to a stop and then stopped at."""

    def __init__(self, project: Path, path: Path) -> None:
        self.project = project
        self.path = path

    def stop(
        self,
        transcript: str,
        final_reply: str,
        env: dict[str, str] | None = None,
        *,
        written_later: str | None = None,
    ) -> list[subprocess.CompletedProcess[str]]:
        """Stops with `transcript` written; `written_later` replaces it a moment after."""
        self.path.write_text(transcript)
        if written_later is not None:
            threading.Timer(0.5, self.path.write_text, [written_later]).start()
        return fire_stop_hooks(self.project, self.path, final_reply, env or {})


@pytest.fixture
def session(project: Path, tmp_path: Path) -> Session:
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    return Session(project, transcripts / f"{SESSION_ID}.jsonl")


def fire_stop_hooks(
    project: Path, transcript: Path, final_reply: str, env: dict[str, str]
) -> list[subprocess.CompletedProcess[str]]:
    """Runs each Stop hook in the project's settings.json as Claude Code does."""
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    hook_input = {
        "session_id": SESSION_ID,
        "transcript_path": str(transcript),
        "cwd": str(project),
        "permission_mode": "auto",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": final_reply,
        "background_tasks": [],
        "session_crons": [],
    }
    base = {k: v for k, v in os.environ.items() if k not in CAPTURE_VARS}
    results = []
    for group in settings["hooks"]["Stop"]:
        for hook in group["hooks"]:
            assert hook["type"] == "command"
            results.append(
                subprocess.run(
                    [hook["command"], *hook["args"]],
                    input=json.dumps(hook_input),
                    capture_output=True,
                    text=True,
                    cwd=project,
                    env={**base, "CLAUDE_PROJECT_DIR": str(project), **env},
                    timeout=hook.get("timeout", 600),
                    check=False,
                )
            )
    return results


def files_under(path: Path) -> list[Path]:
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def test_with_capture_off_the_hook_writes_nothing(session: Session, inbox: Path):
    # Off is the default: naming an inbox doesn't turn capture on.
    results = session.stop(TWO_EXCHANGES, INSTALL_REPLY, env={"PA_CAPTURE_INBOX": str(inbox)})

    assert [r.returncode for r in results] == [0]
    assert files_under(inbox.parent.parent) == []


def until_line(transcript: str, count: int) -> str:
    return "".join(transcript.splitlines(keepends=True)[:count])


def capture_on(inbox: Path) -> dict[str, str]:
    return {"PA_CAPTURE_EXCHANGES": "1", "PA_CAPTURE_INBOX": str(inbox)}


def test_with_capture_on_each_stop_drops_its_exchange_into_the_inbox(session: Session, inbox: Path):
    first = session.stop(until_line(TWO_EXCHANGES, 23), QUOTE_REPLY, env=capture_on(inbox))
    second = session.stop(TWO_EXCHANGES, INSTALL_REPLY, env=capture_on(inbox))

    assert [r.returncode for r in first + second] == [0, 0], [r.stderr for r in first + second]
    # One payload per exchange, named by its episode id; nothing half-written is left behind.
    payloads = files_under(inbox)
    assert [p.suffix for p in payloads] == [".json", ".json"]
    assert all(p.name.startswith("assistant_chat_") for p in payloads)
    turns = sorted(json.loads((inbox / p).read_text())["turns"][0]["text"] for p in payloads)
    assert turns == [
        "What did Rick say about the countertop quote, and did we go with it?",
        "When are they installing it?",
    ]


def test_ingest_lands_a_sessions_exchanges_as_episodes_in_one_thread_and_dedupes_a_refire(
    session: Session, inbox: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    data_root = inbox.parent.parent
    monkeypatch.setenv("PA_DATA_DIR", str(data_root))
    monkeypatch.setenv("PA_OWNER_NAME", OWNER.name)
    monkeypatch.setenv("PA_OWNER_EMAILS", OWNER.identifier)
    session.stop(until_line(TWO_EXCHANGES, 23), QUOTE_REPLY, env=capture_on(inbox))
    session.stop(TWO_EXCHANGES, INSTALL_REPLY, env=capture_on(inbox))

    assert main(["ingest"]) == 0
    # The hook fires again for the second exchange, after ingest took the first payload.
    session.stop(TWO_EXCHANGES, INSTALL_REPLY, env=capture_on(inbox))
    assert main(["ingest"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "ingested 2, unchanged 0, quarantined 0",
        "ingested 0, unchanged 1, quarantined 0",
    ]
    episodes = [
        json.loads(p.read_text())
        for p in sorted((data_root / "l0" / "episodes" / "assistant_chat").rglob("*.json"))
    ]
    assert [(e["source"], e["kind"], e["thread_ref"]) for e in episodes] == [
        ("assistant_chat", "chat", SESSION_ID),
        ("assistant_chat", "chat", SESSION_ID),
    ]


def test_the_hook_waits_for_the_reply_to_reach_the_transcript(session: Session, inbox: Path):
    # Claude Code writes the transcript asynchronously: at the stop, the reply may not be in it.
    results = session.stop(
        until_line(TWO_EXCHANGES, 26),
        INSTALL_REPLY,
        env=capture_on(inbox),
        written_later=TWO_EXCHANGES,
    )

    assert [r.returncode for r in results] == [0], [r.stderr for r in results]
    [payload] = files_under(inbox)
    assert json.loads((inbox / payload).read_text())["turns"][-1]["text"] == INSTALL_REPLY


def test_a_reply_that_never_reaches_the_transcript_is_reported_and_nothing_is_written(
    session: Session, inbox: Path, capsys: pytest.CaptureFixture[str]
):
    # The hook's own entry point, with a short wait instead of the hook's full one.
    session.path.write_text(until_line(TWO_EXCHANGES, 26))
    stop = {"transcript_path": str(session.path), "last_assistant_message": INSTALL_REPLY}

    status = run_stop_hook(json.dumps(stop).encode(), capture_on(inbox), wait_seconds=0.3)

    assert status == 1
    assert "doesn't end with the reply" in capsys.readouterr().err
    assert files_under(inbox) == []


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"PA_CAPTURE_EXCHANGES": "1"}, "PA_CAPTURE_INBOX is not set"),
        ({"PA_CAPTURE_EXCHANGES": "1", "PA_CAPTURE_INBOX": "relative/inbox"}, "absolute"),
        ({"PA_CAPTURE_EXCHANGES": "yes", "PA_CAPTURE_INBOX": "{inbox}"}, "1 (on) or 0 (off)"),
    ],
    ids=["no-inbox", "relative-inbox", "unclear-switch"],
)
def test_capture_turned_on_but_misconfigured_says_why_without_stopping_the_session(
    session: Session, inbox: Path, env: dict[str, str], reason: str
):
    env = {name: value.format(inbox=inbox) for name, value in env.items()}

    [result] = session.stop(TWO_EXCHANGES, INSTALL_REPLY, env=env)

    # Exit status 2 would block the stop and make the assistant carry on; 1 is only reported.
    assert result.returncode == 1
    assert reason in result.stderr
    assert files_under(inbox.parent.parent) == []


def test_capture_into_an_inbox_directory_that_is_missing_fails_rather_than_creating_it(
    session: Session, tmp_path: Path
):
    # In the VM the inbox directory is a mount: a missing one means the mount is missing.
    missing = tmp_path / "unmounted" / "assistant_chat"

    [result] = session.stop(TWO_EXCHANGES, INSTALL_REPLY, env=capture_on(missing))

    assert result.returncode == 1
    assert "not an existing" in result.stderr
    assert not missing.exists()


def with_origin(transcript: str, line: int, kind: str) -> str:
    """`transcript` with the message on `line` (counted from 1) sent from another origin."""
    lines = transcript.splitlines(keepends=True)
    lines[line - 1] = lines[line - 1].replace(
        '"origin":{"kind":"human"}', f'"origin":{{"kind":"{kind}"}}'
    )
    return "".join(lines)


@pytest.mark.parametrize(
    ("transcript", "final_reply"),
    [
        (with_origin(TWO_EXCHANGES, 25, "channel"), INSTALL_REPLY),
        (
            until_line((TRANSCRIPTS / "declined_question.jsonl").read_text(), 8),
            "It's Saturday, October 10, a little after 9:10 in the morning.",
        ),
    ],
    ids=["unknown-origin", "shell-command"],
)
def test_a_stop_with_nothing_to_capture_says_so_without_failing(
    session: Session, inbox: Path, transcript: str, final_reply: str
):
    # No owner message is recognised in the turn: a message from an origin capture doesn't
    # know, or the owner ran a shell command from the prompt.
    [result] = session.stop(transcript, final_reply, env=capture_on(inbox))

    assert result.returncode == 0
    assert result.stderr.count("\n") == 1
    assert "exchange capture: nothing captured" in result.stderr
    assert files_under(inbox) == []
