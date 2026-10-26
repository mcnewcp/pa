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
import json, os, shlex, sys
calls = os.environ["CALLS"]
number = len(os.listdir(calls))
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" else b""
with open(os.path.join(calls, f"{number:02}.json"), "w") as call:
    json.dump({"argv": argv, "stdin": stdin.hex()}, call)
# The VM's directories, as far as the script can learn or change them: those a push moved into
# place (`... && mv -T PARTIAL DIR`), kept in a file between calls.
vm_dirs = os.environ["VM_DIRS"]
known = open(vm_dirs).read().split() if os.path.exists(vm_dirs) else []
command = argv[-1] if argv[0] == "exec" else ""
words = shlex.split(command)
if words[:2] == ["test", "-d"]:
    sys.exit(0 if words[2] in known else 1)
if "-T" in words and words[words.index("-T") - 1] == "mv":
    with open(vm_dirs, "a") as listed:
        listed.write(words[-1] + "\\n")
if command.startswith("bash -s"):
    print("run directory: /home/assistant/.cache/pa-eval/runs/2026-10-19T000000Z")
"""


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    directory = tmp_path / "calls"
    directory.mkdir()
    return directory


DEFAULT = Path("<the default>")
"""For `runs_dir`: leave PA_EVAL_RUNS_DIR unset."""


def run_evals(
    tmp_path: Path, calls: Path, *args: str, runs_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
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
        parents=True, exist_ok=True
    )
    environ = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(calls),
        "VM_DIRS": str(tmp_path / "vm-dirs"),
        # The run directory comes back here, never into the checkout's evals/runs/.
        "PA_EVAL_RUNS_DIR": str(tmp_path / "runs"),
    }
    if runs_dir == DEFAULT:
        del environ["PA_EVAL_RUNS_DIR"]
    elif runs_dir is not None:
        environ["PA_EVAL_RUNS_DIR"] = str(runs_dir)
    return subprocess.run(
        ["bash", str(RUN_EVALS), *args], capture_output=True, text=True, env=environ, check=False
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


# Runs over a cut (#90): the cut is pushed into the VM once and reused, the eval set each run.

VM_CUTS = "/home/assistant/.cache/pa-eval/cuts"
VM_EVAL_SET = "/home/assistant/.cache/pa-eval/eval-set.json"
VM_RUNS = "/home/assistant/.cache/pa-eval/runs"
CUT_NAME = "20261026T120000Z"


def data_root(tmp_path: Path) -> Path:
    """The instance's data root, under the stand-in home."""
    return tmp_path / "home" / ".local" / "share" / "pa-home-synthetic"


@pytest.fixture
def cut(tmp_path: Path) -> Path:
    """A cut under the data root, as `pa cut` lays one out (its contents made up)."""
    path = data_root(tmp_path) / "cuts" / CUT_NAME
    (path / "l0" / "episodes" / "gmail" / "2026-10").mkdir(parents=True)
    (path / "l0" / "episodes" / "gmail" / "2026-10" / "gmail_0123456789abcdef.json").write_text(
        "{}\n"
    )
    (path / "cut.json").write_text('{"as_of": "2026-10-26T12:00:00Z"}\n')
    return path


@pytest.fixture
def eval_set(tmp_path: Path) -> Path:
    path = data_root(tmp_path) / "evals" / "real.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"questions": []}\n')
    return path


def over(cut: Path, eval_set: Path) -> list[str]:
    return ["--agent", "claude", "--judge", "model", "--cut", str(cut), "--eval-set", str(eval_set)]


def test_a_run_over_a_cut_pushes_the_cut_and_the_set_and_asks_with_the_vms_copies(
    tmp_path: Path, calls: Path, cut: Path, eval_set: Path
):
    result = run_evals(tmp_path, calls, *over(cut, eval_set), runs_dir=DEFAULT)

    assert result.returncode == 0, result.stderr
    check, push_cut, push_set, ask, pull = recorded(calls)
    assert check["argv"][-1] == f"test -d {VM_CUTS}/{CUT_NAME}"
    assert pushed(push_cut) == [
        "./cut.json",
        "./l0/episodes/gmail/2026-10/gmail_0123456789abcdef.json",
    ]
    assert push_cut["argv"][-1].endswith(
        f"mv -T {VM_CUTS}/.{CUT_NAME}.partial {VM_CUTS}/{CUT_NAME}"
    )
    assert bytes.fromhex(push_set["stdin"]) == eval_set.read_bytes()
    assert VM_EVAL_SET in push_set["argv"][-1]
    assert pa_eval_args(ask) == [
        "--agent",
        "claude",
        "--judge",
        "model",
        "--cut",
        f"{VM_CUTS}/{CUT_NAME}",
        "--eval-set",
        VM_EVAL_SET,
        "--runs-dir",
        VM_RUNS,
    ]
    # A real-data run comes back under the data root, never into the checkout.
    assert pull["argv"][-1] == f"{data_root(tmp_path) / 'evals' / 'runs'}/"


def test_a_cut_already_in_the_vm_is_reused_and_not_pushed_again(
    tmp_path: Path, calls: Path, cut: Path, eval_set: Path
):
    assert run_evals(tmp_path, calls, *over(cut, eval_set)).returncode == 0
    first = len(recorded(calls))

    result = run_evals(tmp_path, calls, *over(cut, eval_set))

    assert result.returncode == 0, result.stderr
    check, push_set, ask, _ = recorded(calls)[first:]
    assert check["argv"][-1] == f"test -d {VM_CUTS}/{CUT_NAME}"
    assert bytes.fromhex(push_set["stdin"]) == eval_set.read_bytes()
    assert f"--cut {VM_CUTS}/{CUT_NAME}" in ask["argv"][-1]
    assert "already in the VM" in result.stderr


def test_a_cut_run_without_its_eval_set_stops_before_the_vm_is_touched(
    tmp_path: Path, calls: Path, cut: Path
):
    result = run_evals(tmp_path, calls, "--agent", "claude", "--judge", "model", "--cut", str(cut))

    assert result.returncode != 0
    assert "--cut needs --eval-set" in result.stderr
    assert recorded(calls) == []


def test_a_directory_that_isnt_a_cut_stops_before_the_vm_is_touched(
    tmp_path: Path, calls: Path, eval_set: Path
):
    empty = tmp_path / "empty"
    empty.mkdir()

    result = run_evals(tmp_path, calls, *over(empty, eval_set))

    assert result.returncode != 0
    assert f"{empty} isn't a cut" in result.stderr
    assert recorded(calls) == []


INSIDE = DEPLOY.parents[1] / "evals" / "runs" / "private-test-never-written"


@pytest.mark.parametrize("which", ["cut", "eval set", "runs directory"])
def test_real_data_paths_inside_the_repository_stop_before_the_vm_is_touched(
    tmp_path: Path, calls: Path, cut: Path, eval_set: Path, which: str
):
    if which == "cut":
        INSIDE.mkdir(parents=True)
        (INSIDE / "cut.json").write_text("{}\n")
    try:
        result = run_evals(
            tmp_path,
            calls,
            *over(INSIDE if which == "cut" else cut, INSIDE if which == "eval set" else eval_set),
            runs_dir=INSIDE if which == "runs directory" else None,
        )
    finally:
        shutil.rmtree(INSIDE, ignore_errors=True)

    assert result.returncode != 0
    assert "is inside the repository" in result.stderr
    assert recorded(calls) == []
