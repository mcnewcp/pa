"""run-evals.sh with a files-plus-wiki run: the eval wiki is pushed into the VM, and the run
asks with the VM's copy. It runs here on a stand-in `incus` (and `sudo`) that records each call
and what was piped to it."""

from __future__ import annotations

import io
import json
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
RUN_EVALS = DEPLOY / "run-evals.sh"
EVAL_WIKI = DEPLOY.parents[1] / "evals" / "tests" / "fixtures" / "eval-wiki"
VM_EVAL_WIKI = "/home/assistant/.cache/pa-eval/eval-wiki"

STAND_IN_INCUS = """\
import json, os, sys
calls = os.environ["CALLS"]
number = len(os.listdir(calls))
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" else b""
with open(os.path.join(calls, f"{number:02}.json"), "w") as call:
    json.dump({"argv": argv, "stdin": stdin.hex()}, call)
if argv[0] == "exec" and argv[-1].startswith("bash -s"):
    print("run directory: /home/assistant/.cache/pa-eval/runs/2026-10-19T000000Z")
"""


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    directory = tmp_path / "calls"
    directory.mkdir()
    return directory


def run_evals(tmp_path: Path, calls: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    incus = bin_dir / "incus"
    incus.write_text(f"#!{sys.executable}\n{STAND_IN_INCUS}")
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n')
    for script in (incus, sudo):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    (home / ".local" / "share" / "pa-home-synthetic" / "inbox" / "assistant_chat").mkdir(
        parents=True
    )
    return subprocess.run(
        ["bash", str(RUN_EVALS), *args],
        capture_output=True,
        text=True,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(calls),
            # The run directory comes back here, never into the checkout's evals/runs/.
            "PA_EVAL_RUNS_DIR": str(tmp_path / "runs"),
        },
        check=False,
    )


def recorded(calls: Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(calls.iterdir())]


def pa_eval_args(call: dict) -> list[str]:
    """The `pa-eval run` options of the call that runs it as the assistant."""
    _bash, _s, _dashes, repo, *options = shlex.split(call["argv"][-1])
    assert repo == "/home/assistant/pa"
    return options


def pushed(call: dict) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(bytes.fromhex(call["stdin"]))) as archive:
        return sorted(member.name for member in archive.getmembers() if member.isfile())


@pytest.fixture
def replay(tmp_path: Path) -> Path:
    """A replay's target: the eval wiki, with the replay's git directory beside it."""
    target = tmp_path / "replay"
    shutil.copytree(EVAL_WIKI, target)
    (target / "wiki.git").mkdir()
    (target / "wiki.git" / "HEAD").write_text("ref: refs/heads/main\n")
    return target


def test_a_files_plus_wiki_run_pushes_the_eval_wiki_and_asks_with_the_vms_copy(
    tmp_path: Path, calls: Path, replay: Path
):
    options = ["--agent", "claude", "--judge", "model", "--configuration", "files-plus-wiki"]

    result = run_evals(tmp_path, calls, *options, "--wiki", str(replay), "--corpus", "scale")

    assert result.returncode == 0, result.stderr
    push, ask, pull = recorded(calls)
    assert pushed(push) == [
        "run-log.jsonl",
        "wiki/days/2026-09-14.md",
        "wiki/index.md",
        "wiki/topics/daves-wedding.md",
    ]
    assert push["argv"][:6] == ["exec", "pa-home", "--", "runuser", "-l", "assistant"]
    assert f"tar -C {VM_EVAL_WIKI} -xf -" in push["argv"][-1]
    assert pa_eval_args(ask) == [*options, "--wiki", VM_EVAL_WIKI, "--corpus", "scale"]
    assert pull["argv"][:3] == ["file", "pull", "-r"]
    assert pull["argv"][-1] == f"{tmp_path / 'runs'}/"
    assert (tmp_path / "runs").is_dir()


def test_the_wiki_option_can_be_given_with_an_equals_sign(
    tmp_path: Path, calls: Path, replay: Path
):
    options = ["--agent", "claude", "--judge", "model", "--configuration", "files-plus-wiki"]

    result = run_evals(tmp_path, calls, *options, f"--wiki={replay}")

    assert result.returncode == 0, result.stderr
    push, ask, _ = recorded(calls)
    assert "run-log.jsonl" in pushed(push)
    assert pa_eval_args(ask) == [*options, "--wiki", VM_EVAL_WIKI]


def test_a_run_without_a_wiki_pushes_nothing(tmp_path: Path, calls: Path):
    result = run_evals(tmp_path, calls)

    assert result.returncode == 0, result.stderr
    ask, _ = recorded(calls)
    assert pa_eval_args(ask) == ["--agent", "claude", "--judge", "model"]


def test_a_wiki_directory_that_isnt_an_eval_wiki_stops_before_the_vm_is_touched(
    tmp_path: Path, calls: Path
):
    empty = tmp_path / "empty"
    empty.mkdir()

    result = run_evals(
        tmp_path, calls, "--agent", "claude", "--judge", "model", "--wiki", str(empty)
    )

    assert result.returncode != 0
    assert f"{empty} isn't an eval wiki" in result.stderr
    assert recorded(calls) == []
