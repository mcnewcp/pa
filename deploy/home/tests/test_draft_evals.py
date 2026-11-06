"""draft-evals.sh: a session in the assistant's VM drafts the real-data eval set from a cut, and
the draft comes back under the data root. It runs here on a stand-in `incus` (and `sudo`) that
records each call and what was piped to it, and pulls back a made-up draft."""

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
DRAFT_EVALS = DEPLOY / "draft-evals.sh"

VM_CUTS = "/srv/pa/eval/cuts"
VM_DRAFTS = "/srv/pa/eval/drafts"
VM_EVAL_TMP = "/srv/pa/eval/tmp"
VM_OWNER_ENV = "/etc/pa/owner.env"
CUT_NAME = "20261026T120000Z"

STAND_IN_INCUS = """\
import json, os, shlex, sys
calls = os.environ["CALLS"]
number = len(os.listdir(calls))
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" else b""
with open(os.path.join(calls, f"{number:02}.json"), "w") as call:
    json.dump({"argv": argv, "stdin": stdin.hex()}, call)
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
    sys.exit(int(os.environ.get("DRAFT_EXIT", "0")))
if argv[:2] == ["file", "pull"]:
    with open(argv[-1], "w") as pulled:
        pulled.write('{"questions": []}\\n')
"""


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    directory = tmp_path / "calls"
    directory.mkdir()
    return directory


def data_root(tmp_path: Path) -> Path:
    """The instance's data root, under the stand-in home."""
    return tmp_path / "home" / ".local" / "share" / "pa-home"


def draft_evals(
    tmp_path: Path,
    calls: Path,
    *args: str,
    draft_exit: int = 0,
    stand_in: str = STAND_IN_INCUS,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    incus = bin_dir / "incus"
    incus.write_text(f"#!{sys.executable}\n{stand_in}")
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n')
    for script in (incus, sudo):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (data_root(tmp_path) / "inbox" / "assistant_chat").mkdir(parents=True, exist_ok=True)
    environ = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(calls),
        "VM_DIRS": str(tmp_path / "vm-dirs"),
        "DRAFT_EXIT": str(draft_exit),
    }
    return subprocess.run(
        ["bash", str(DRAFT_EVALS), *args], capture_output=True, text=True, env=environ, check=False
    )


def recorded(calls: Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(calls.iterdir())]


def as_assistant(call: dict) -> list[str]:
    """The arguments of the script run as the assistant: the repository, owner.env, the
    session's TMPDIR, then the `pa-eval draft` options."""
    _bash, _s, _dashes, *arguments = shlex.split(call["argv"][-1])
    return arguments


def script_of(call: dict) -> str:
    return bytes.fromhex(call["stdin"]).decode()


def pushed(call: dict) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(bytes.fromhex(call["stdin"]))) as archive:
        return sorted(member.name for member in archive.getmembers() if member.isfile())


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


def test_a_draft_pushes_the_cut_drafts_in_the_vm_and_comes_back_under_the_data_root(
    tmp_path: Path, calls: Path, cut: Path
):
    result = draft_evals(tmp_path, calls, "--cut", str(cut))

    assert result.returncode == 0, result.stderr
    check, push_cut, ask, pull = recorded(calls)
    assert check["argv"][-1] == f"test -d {VM_CUTS}/{CUT_NAME}"
    assert pushed(push_cut) == [
        "./cut.json",
        "./l0/episodes/gmail/2026-10/gmail_0123456789abcdef.json",
    ]
    assert ask["argv"][:6] == ["exec", "pa-home", "--", "runuser", "-l", "assistant"]
    repository, owner_env, tmp, *options = as_assistant(ask)
    assert (repository, owner_env) == ("/opt/pa", VM_OWNER_ENV)
    # Outside the assistant's home, whose CLAUDE.md files the managed settings exclude.
    assert tmp == VM_EVAL_TMP
    [vm_draft] = [options[i + 1] for i, option in enumerate(options) if option == "--out"]
    assert vm_draft.startswith(f"{VM_DRAFTS}/draft-")
    assert options == [
        "--agent",
        "claude",
        "--cut",
        f"{VM_CUTS}/{CUT_NAME}",
        "--out",
        vm_draft,
    ]
    # The session drafts as the owner in owner.env.
    assert 'source "$owner_env"' in script_of(ask)
    assert ".venv/bin/pa-eval draft" in script_of(ask)
    assert pull["argv"][:2] == ["file", "pull"]
    assert pull["argv"][2] == f"pa-home{vm_draft}"
    [drafted] = (data_root(tmp_path) / "evals" / "drafts" / CUT_NAME).iterdir()
    assert pull["argv"][3] == str(drafted)
    assert drafted.name == vm_draft.rsplit("/", 1)[1]
    assert stat.S_IMODE(drafted.stat().st_mode) == 0o600
    assert str(drafted) in result.stderr


def test_a_cut_already_in_the_vm_is_reused(tmp_path: Path, calls: Path, cut: Path):
    (tmp_path / "vm-dirs").write_text(f"{VM_CUTS}/{CUT_NAME}\n")

    result = draft_evals(tmp_path, calls, "--cut", str(cut), "--agent-model", "sonnet")

    assert result.returncode == 0, result.stderr
    _check, ask, _pull = recorded(calls)
    assert as_assistant(ask)[3:7] == ["--agent", "claude", "--agent-model", "sonnet"]
    assert "pushed the cut" not in result.stderr


def test_a_second_pass_pushes_the_draft_and_the_notes_and_revises_with_the_vms_copies(
    tmp_path: Path, calls: Path, cut: Path
):
    drafts = data_root(tmp_path) / "evals" / "drafts" / CUT_NAME
    drafts.mkdir(parents=True)
    first = drafts / "draft-20261027T090000Z.json"
    first.write_text('{"questions": ["as the owner left it"]}\n')
    notes = drafts / "notes.md"
    notes.write_text("Made-up notes.\n")

    result = draft_evals(
        tmp_path, calls, "--cut", str(cut), "--revise", str(first), "--notes", str(notes)
    )

    assert result.returncode == 0, result.stderr
    _check, _push_cut, push_draft, push_notes, ask, _pull = recorded(calls)
    assert bytes.fromhex(push_draft["stdin"]) == first.read_bytes()
    assert f"cat > {VM_DRAFTS}/revise.json" in push_draft["argv"][-1]
    assert bytes.fromhex(push_notes["stdin"]) == notes.read_bytes()
    assert f"cat > {VM_DRAFTS}/notes.md" in push_notes["argv"][-1]
    options = as_assistant(ask)[3:]
    assert options[options.index("--revise") + 1] == f"{VM_DRAFTS}/revise.json"
    assert options[options.index("--notes") + 1] == f"{VM_DRAFTS}/notes.md"
    assert len(list(drafts.glob("draft-*.json"))) == 2
    assert first.read_text() == '{"questions": ["as the owner left it"]}\n'


def test_a_second_pass_needs_both_the_draft_and_the_notes(tmp_path: Path, calls: Path, cut: Path):
    notes = data_root(tmp_path) / "evals" / "notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Made-up notes.\n")

    result = draft_evals(tmp_path, calls, "--cut", str(cut), "--notes", str(notes))

    assert result.returncode != 0
    assert "--revise DRAFT and --notes FILE" in result.stderr
    assert recorded(calls) == []


def test_a_failed_draft_brings_nothing_back(tmp_path: Path, calls: Path, cut: Path):
    result = draft_evals(tmp_path, calls, "--cut", str(cut), draft_exit=1)

    assert result.returncode != 0
    assert [call["argv"][0] for call in recorded(calls)] == ["exec", "exec", "exec"]
    assert not (data_root(tmp_path) / "evals" / "drafts" / CUT_NAME).exists() or not list(
        (data_root(tmp_path) / "evals" / "drafts" / CUT_NAME).iterdir()
    )


def test_a_directory_that_isnt_a_cut_stops_before_the_vm_is_touched(tmp_path: Path, calls: Path):
    empty = data_root(tmp_path) / "cuts" / "empty"
    empty.mkdir(parents=True)

    result = draft_evals(tmp_path, calls, "--cut", str(empty))

    assert result.returncode != 0
    assert "isn't a cut" in result.stderr
    assert recorded(calls) == []


INSIDE = DEPLOY.parents[1] / "evals" / "runs" / "private-draft-test-never-written"


@pytest.mark.parametrize("which", ["cut", "draft", "notes"])
def test_real_data_paths_inside_the_repository_stop_before_the_vm_is_touched(
    tmp_path: Path, calls: Path, cut: Path, which: str
):
    private = data_root(tmp_path) / "evals" / "drafts"
    private.mkdir(parents=True)
    INSIDE.mkdir(parents=True)
    paths = {"cut": cut, "draft": private / "draft-1.json", "notes": private / "notes.md"}
    paths[which] = INSIDE if which == "cut" else INSIDE / f"{which}.txt"
    try:
        (INSIDE / "cut.json").write_text("{}\n")
        for name in ("draft", "notes"):
            paths[name].write_text("{}\n")
        result = draft_evals(
            tmp_path,
            calls,
            *("--cut", str(paths["cut"])),
            *("--revise", str(paths["draft"]), "--notes", str(paths["notes"])),
        )
    finally:
        shutil.rmtree(INSIDE, ignore_errors=True)

    assert result.returncode != 0
    assert "is inside the repository" in result.stderr
    assert recorded(calls) == []


def test_the_vms_inbox_changing_during_the_draft_fails_it(tmp_path: Path, calls: Path, cut: Path):
    """Capture is off in the drafting session, so nothing it says comes back into L0: an inbox
    that changed while it ran fails the draft."""
    inbox = data_root(tmp_path) / "inbox" / "assistant_chat"
    stand_in = STAND_IN_INCUS.replace(
        'if command.startswith("bash -s"):\n',
        'if command.startswith("bash -s"):\n'
        f"    open({str(inbox / 'exchange.json')!r}, 'w').write('{{}}')\n",
    )
    result = draft_evals(tmp_path, calls, "--cut", str(cut), stand_in=stand_in)

    assert result.returncode != 0
    assert "changed" in result.stderr
    assert [call["argv"][0] for call in recorded(calls)] == ["exec", "exec", "exec"]
