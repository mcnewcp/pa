"""deploy/home/diagnose.sh, whole, with the real commands on both sides: `pa-eval wiki-history`
and `pa-eval release` on the devbox, and `pa-eval diagnose` where the VM would run it.

The VM is played on this machine by a stand-in `incus`: a command for the VM runs here, with
the VM's eval directory mapped into the test's temporary directory and its checkout of the code
mapped to this one, and a pull copies from there. The session is a scripted one, so no `claude`
runs. The script's own tests (deploy/home/tests/test_diagnose.py) cover what it pushes and how
it ends; this holds it to the commands it runs.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pa_evals.findings import parse_findings
from pa_home.config import DataRoot
from pa_home.cut import Cut
from pa_home.git_wiki import GitWikiRepository

REPOSITORY = Path(__file__).resolve().parents[2]
DIAGNOSE = REPOSITORY / "deploy" / "home" / "diagnose.sh"

REFUSED = 3
FAILED = 4

PLAYED_VM = """\
import os, shutil, subprocess, sys
root, code = os.environ["VM_ROOT"], os.environ["VM_CODE"]
argv = sys.argv[1:]


def here(text):
    return text.replace("/srv/pa/eval", root + "/srv/pa/eval").replace("/opt/pa", code)


if argv[0] == "exec":
    # exec VM -- runuser -l assistant -c COMMAND: the command, with what a login gives it.
    assert argv[2:7] == ["--", "runuser", "-l", "assistant", "-c"], argv
    login = {"PATH": "/usr/bin:/bin", "HOME": root + "/home/assistant"}
    sys.exit(subprocess.run(["bash", "-c", here(argv[7])], env=login).returncode)
if argv[:3] == ["file", "pull", "-r"]:
    source = here("/" + argv[3].split("/", 1)[1])
    shutil.copytree(source, os.path.join(argv[4], os.path.basename(source)), symlinks=True)
"""


@pytest.fixture
def diagnose_sh(
    tmp_path: Path, data_root: DataRoot, cut: Cut, referral: Path, unsalted: Callable[[str], None]
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Runs diagnose.sh over the cut, with the given options, on the instance the cut was taken
    from. Nothing it prints may hold a word a session wrote or the case holds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_ins = {
        "incus": f"#!{sys.executable}\n{PLAYED_VM}",
        "sudo": '#!/bin/bash\nexec "$@"\n',
        # The nightly job's unit, which isn't running.
        "systemctl": "#!/bin/bash\necho inactive\n",
    }
    for name, text in stand_ins.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (data_root.path / "inbox" / "assistant_chat").mkdir(parents=True, exist_ok=True)
    (data_root.path / "owner.env").write_text(
        "PA_OWNER_NAME='Argus McNevans'\nPA_OWNER_EMAILS=argus@example.com\n"
        "PA_OWNER_OTHER_NAMES=Gus\n"
    )

    def run(*options: object, name: str = "dreamed-drop") -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                *("bash", str(DIAGNOSE), "--referral", str(referral), "--name", name),
                *("--cut", str(cut.path), *map(str, options)),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                "HOME": str(tmp_path / "home"),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "PA_DATA_ROOT": str(data_root.path),
                "PA_RELEASED_DIR": str(tmp_path / "released"),
                "VM_ROOT": str(tmp_path / "vm"),
                "VM_CODE": str(REPOSITORY),
            },
        )
        unsalted(result.stdout)
        unsalted(result.stderr)
        return result

    return run


def modes(directory: Path) -> set[tuple[str, int]]:
    """Whether each thing under `directory` is a file, with its mode."""
    return {
        ("file" if path.is_file() else "directory", stat.S_IMODE(path.stat().st_mode))
        for path in (directory, *directory.rglob("*"))
    }


PRIVATE = {("directory", 0o700), ("file", 0o600)}


def test_a_diagnosis_over_a_whole_case_is_released_by_the_real_commands(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    parts: Any,
    leavings: Callable[..., Path],
    well_formed: dict[str, Any],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    assert cut.wiki is not None
    copy = tmp_path / "replays" / "dreamed"
    dreamed = GitWikiRepository.open(copy / "wiki", copy / "wiki.git")
    shutil.copytree(cut.wiki, copy / "wiki", dirs_exist_ok=True)
    dreamed.commit("Start the copy to dream\n")

    result = diagnose_sh(
        *parts.arguments(),
        *("--wiki", f"own={cut.path}", "--wiki", f"dreamed={copy}"),
        *("--agent", "scripted", "--leaves", leavings()),
    )

    assert result.returncode == 0, result.stderr
    [private] = data_root.diagnoses.iterdir()
    name = private.name
    released = tmp_path / "released" / name
    assert result.stdout.splitlines() == [
        f"diagnosis: {name}, over the cut {cut.name}",
        # Each wiki's history is its start and one commit: the batch the cut's fixture
        # observed, and the copy's.
        "case: an eval set; runs own (3), dreamed (6); "
        "wikis own (2 runs of history), dreamed (2 runs of history)",
        "session: cost not reported, turns not reported",
        "resumes: 0",
        f"left: {private}",
        f"release: {name}: released",
        f"released: {released}",
        "findings: 2 (1 fault, 1 noise)",
        "reproductions: 0",
        "payloads: 0",
        *(f"check {rule}: 0" for rule in rules),
    ]
    # The private analysis stays under the data root, for the owner alone; the findings a
    # development session reads are the checked ones.
    assert modes(private) == PRIVATE
    assert (private / "output" / "analysis.md").is_file()
    assert not (released / "analysis.md").exists()
    assert parse_findings((released / "findings.json").read_bytes()) == parse_findings(
        json.dumps(well_formed).encode()
    )
    record = json.loads((released / "release.json").read_text())
    assert (record["name"], record["cut"]) == (name, cut.name)
    # The session read the VM's copies, laid in a throwaway directory that is gone.
    vm = tmp_path / "vm" / "srv" / "pa" / "eval"
    assert (vm / "cuts" / cut.name / "cut.json").is_file()
    assert sorted(path.name for path in (vm / "case" / "runs" / "dreamed" / "1").iterdir()) == [
        f"run-{number}" for number in range(1, 7)
    ]
    assert (vm / "case" / "wikis" / "dreamed" / "wiki").is_dir()
    assert not list(vm.rglob("wiki.git"))
    assert list((vm / "tmp").iterdir()) == []
    # Each wiki's exported history went with the diagnosis that made it.
    assert list(data_root.diagnoses.iterdir()) == [private]


def test_a_follow_up_diagnosis_is_given_the_earlier_ones_private_analysis(
    tmp_path: Path,
    data_root: DataRoot,
    parts: Any,
    leavings: Callable[..., Path],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    scripted = ("--agent", "scripted", "--leaves", leavings())
    first = diagnose_sh(*parts.arguments(), *scripted)
    [earlier] = data_root.diagnoses.iterdir()

    second = diagnose_sh(*parts.arguments(), "--earlier", earlier, *scripted, name="follow-up")

    assert (first.returncode, second.returncode) == (0, 0), first.stderr + second.stderr
    assert (
        f"case: an eval set; runs own (3), dreamed (6); earlier {earlier.name}\n" in second.stdout
    )
    # Of the earlier diagnosis, the VM is given its private analysis and no more.
    pushed = tmp_path / "vm" / "srv" / "pa" / "eval" / "case" / "earlier" / earlier.name
    assert [str(path.relative_to(pushed)) for path in pushed.rglob("*") if path.is_file()] == [
        "output/analysis.md"
    ]
    assert len(list((tmp_path / "released").iterdir())) == 2


def test_findings_the_real_check_refuses_end_the_diagnosis_unreleased_with_the_refused_code(
    tmp_path: Path,
    data_root: DataRoot,
    parts: Any,
    leavings: Callable[..., Path],
    well_formed: dict[str, Any],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    well_formed["findings"][0]["code"] = ["core/src/pa_core/no_such_module.py"]

    result = diagnose_sh(
        *parts.arguments(), *("--agent", "scripted", "--leaves", leavings(well_formed))
    )

    assert result.returncode == REFUSED, result.stderr
    [private] = data_root.diagnoses.iterdir()
    counts = {**dict.fromkeys(rules, 0), "paths": 1}
    assert result.stdout.splitlines()[5:] == [
        f"release: {private.name}: refused, and nothing is released",
        *(f"check {rule}: {count}" for rule, count in counts.items()),
        f"notes: {private}/check-notes.md",
    ]
    assert not (tmp_path / "released").exists() or not list((tmp_path / "released").iterdir())
    assert (private / "check-notes.md").is_file()
    assert modes(private) == PRIVATE


def test_a_session_that_leaves_its_output_out_of_form_ends_with_the_failed_code(
    tmp_path: Path,
    data_root: DataRoot,
    parts: Any,
    leavings: Callable[..., Path],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    result = diagnose_sh(
        *parts.arguments(), *("--agent", "scripted", "--leaves", leavings({"answered": "yes"}))
    )

    assert result.returncode == FAILED, result.stderr
    [private] = data_root.diagnoses.iterdir()
    assert result.stdout == f"left: {private}\n"
    assert "left an output that isn't in form" in result.stderr
    assert "release" not in result.stdout
    assert not (tmp_path / "released").exists()
    # What it left, and what is wrong with it, are the owner's to read; it has no session's
    # record, so nothing can be released from it.
    assert (private / "output-problems.md").is_file()
    assert not (private / "session.json").exists()
    assert modes(private) == PRIVATE
