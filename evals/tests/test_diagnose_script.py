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
    # What a VM that isn't to be trusted adds to a diagnosis after its own command held the
    # output to its form.
    if os.environ.get("VM_ADDS"):
        shutil.copytree(os.environ["VM_ADDS"], source, dirs_exist_ok=True)
    shutil.copytree(source, os.path.join(argv[4], os.path.basename(source)), symlinks=True)
"""


@pytest.fixture
def diagnose_sh(
    tmp_path: Path, data_root: DataRoot, cut: Cut, referral: Path, unsalted: Callable[[str], None]
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Runs diagnose.sh over the cut, with the given options, on the instance the cut was taken
    from. Nothing it prints may hold a word a session wrote or the case holds. `vm_adds` is a
    directory of files the VM adds to the diagnosis as it is pulled."""
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

    def run(
        *options: object, name: str = "dreamed-drop", vm_adds: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
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
                **({"VM_ADDS": str(vm_adds)} if vm_adds else {}),
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
        "revisions: 0",
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


SLIPPED = "core/src/pa_core/gooseberry.py"
"""A path no file in the repository has. A finding that blames it is refused, and the check's
notes quote it: it holds a word nothing the script prints may hold."""


def session_says(private: Path, cut: Cut, *, revisions: int) -> list[str]:
    """What the script prints of a scripted session over the desk's case, and of bringing back
    what it left."""
    return [
        f"diagnosis: {private.name}, over the cut {cut.name}",
        "case: an eval set; runs own (3), dreamed (6)",
        "session: cost not reported, turns not reported",
        "resumes: 0",
        f"revisions: {revisions}",
        f"left: {private}",
    ]


def refusal_says(private: Path, rules: tuple[str, ...]) -> list[str]:
    counts = {**dict.fromkeys(rules, 0), "paths": 1}
    return [
        f"release: {private.name}: refused, and nothing is released",
        *(f"check {rule}: {count}" for rule, count in counts.items()),
        f"notes: {private}/check-notes.md",
    ]


def blamed(findings: Path) -> list[str]:
    """The paths the first finding in a findings file blames."""
    return json.loads(findings.read_text())["findings"][0]["code"]


def test_findings_the_real_check_refuses_are_revised_in_the_vm_and_released_once_it_passes(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    parts: Any,
    leavings: Callable[..., Path],
    well_formed: dict[str, Any],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    revised = leavings()
    well_formed["findings"][0]["code"] = [SLIPPED]

    result = diagnose_sh(
        *parts.arguments(),
        *("--agent", "scripted", "--leaves", leavings(well_formed), "--revised", revised),
    )

    assert result.returncode == 0, result.stderr
    [private] = data_root.diagnoses.iterdir()
    released = tmp_path / "released" / private.name
    # One revision, and each pass's counts: never what the check found.
    assert result.stdout.splitlines() == [
        *session_says(private, cut, revisions=0),
        *refusal_says(private, rules),
        *session_says(private, cut, revisions=1),
        f"release: {private.name}: released",
        f"released: {released}",
        "findings: 2 (1 fault, 1 noise)",
        "reproductions: 0",
        "payloads: 0",
        *(f"check {rule}: 0" for rule in rules),
    ]
    # The refused output and the check's notes went into the VM, for the second pass.
    pushed = tmp_path / "vm" / "srv" / "pa" / "eval" / "case" / "refused"
    assert blamed(pushed / "output" / "findings.json") == [SLIPPED]
    assert f"'{SLIPPED}' isn't in the repository" in (pushed / "check-notes.md").read_text()
    # What is released is the revision, with its count; the notes and the refused pass aren't.
    assert SLIPPED not in (released / "findings.json").read_text()
    assert json.loads((released / "release.json").read_text())["revisions"] == 1
    assert sorted(path.name for path in released.iterdir()) == ["findings.json", "release.json"]
    assert not [path for path in released.rglob("*") if "gooseberry" in path.read_text()]
    # Both passes are kept with the private analysis: the refused one with the check's notes.
    assert sorted(path.name for path in private.iterdir()) == [
        "output",
        "referral.md",
        "refused",
        "reply.md",
        "session.json",
    ]
    assert blamed(private / "refused" / "1" / "output" / "findings.json") == [SLIPPED]
    assert (private / "refused" / "1" / "check-notes.md").read_text() == (
        pushed / "check-notes.md"
    ).read_text()
    assert blamed(private / "output" / "findings.json") == blamed(revised / "findings.json")
    assert modes(private) == PRIVATE
    assert list(data_root.diagnoses.iterdir()) == [private]


def test_findings_the_real_check_refuses_three_times_end_the_diagnosis_unreleased(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    parts: Any,
    leavings: Callable[..., Path],
    well_formed: dict[str, Any],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    well_formed["findings"][0]["code"] = [SLIPPED]

    # A scripted session leaves the same output each time it revises.
    result = diagnose_sh(
        *parts.arguments(), *("--agent", "scripted", "--leaves", leavings(well_formed))
    )

    assert result.returncode == REFUSED, result.stderr
    [private] = data_root.diagnoses.iterdir()
    assert result.stdout.splitlines() == [
        *session_says(private, cut, revisions=0),
        *refusal_says(private, rules),
        *session_says(private, cut, revisions=1),
        *refusal_says(private, rules),
        *session_says(private, cut, revisions=2),
        *refusal_says(private, rules),
    ]
    assert "is still refused after 2 revisions, and nothing is released" in result.stderr
    assert not (tmp_path / "released").exists() or not list((tmp_path / "released").iterdir())
    # Every pass's output is kept with the check's notes on it: two that were revised, and the
    # last.
    passes = [private / "refused" / "1", private / "refused" / "2", private]
    for kept in passes:
        assert blamed(kept / "output" / "findings.json") == [SLIPPED]
        assert f"'{SLIPPED}' isn't in the repository" in (kept / "check-notes.md").read_text()
    assert sorted(path.name for path in (private / "refused").iterdir()) == ["1", "2"]
    assert json.loads((private / "session.json").read_text())["revisions"] == 2
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


# Planted leaks, through the whole command (#158's story 71). The case holds a made-up person,
# her address, and a sentence of hers, which stand in for real content. A scripted session
# plants each in its findings and in a reproduction, with a long number. The check on the
# devbox refuses the diagnosis and its revisions, by rule and count, and nothing the script
# prints holds any of what was planted.
#
# A run of words the repository holds passes the check, and this file is in the repository, so
# the sentence is written backwards here.


def forwards(backwards: str) -> str:
    return " ".join(reversed(backwards.split()))


SENTENCE = forwards("wall left the along cracked kiln big The")
NAMED = "Ottoline Varga"
ADDRESS = "ovarga" + "@" + "sgraffito-works" + "." + "com"
NUMBER = "555-014-2368"
PLANTED = (NAMED, ADDRESS, SENTENCE, NUMBER)

CASE_MAIL = f"""\
Message-ID: <kiln-0001@mail.pottery.example>
Date: Tue, 22 Sep 2026 09:15:00 -0500
From: {NAMED} <ovarga@pottery.example>
To: Argus McNevans <argus@example.com>
Subject: The firing
Content-Type: text/plain; charset="utf-8"

{SENTENCE}, so every piece waits for the small one.
"""


@pytest.fixture
def also_in_the_case(also_in_the_case):
    also_in_the_case.payloads["gmail/kiln.eml"] = CASE_MAIL
    return also_in_the_case


ORDER = """\
Message-ID: <order-1@mail.larder.example>
Date: Mon, 21 Sep 2026 10:05:00 -0500
From: Pru Tanager <pru@larder.example>
To: Argus McNevans <argus@example.com>
Subject: Your preserves order
Content-Type: text/plain; charset="utf-8"

The preserves order ships on Friday, in two boxes.
"""
QUESTIONS = {
    "as_of": "2026-09-22T20:00:00-05:00",
    "owner": {
        "name": "Argus McNevans",
        "email_addresses": ["argus@example.com"],
        "other_names": ["Gus"],
    },
    "time_zone": "America/Chicago",
    "questions": [
        {
            "id": "order-ships",
            "category": "single_fact_recall",
            "question": "When does my preserves order ship?",
            "expected_answer": "Friday.",
            "evidence": ["gmail/order.eml"],
        }
    ],
}


@pytest.fixture
def leaves_a_reproduction(
    leavings: Callable[..., Path], well_formed: dict[str, Any]
) -> Callable[..., Path]:
    """Makes what a scripted session leaves: findings whose first names a reproduction, and
    that reproduction, in form. `planted` goes into the finding's mechanism and into the
    reproduction's mail."""

    def leave(planted: str = "") -> Path:
        well_formed["findings"][0].update(
            reproduction="f1",
            no_reproduction=None,
            mechanism=f"A merge keeps the older page's summary. {planted}".strip(),
        )
        left = leavings(well_formed)
        reproduction = left / "reproductions" / "f1"
        (reproduction / "payloads" / "gmail").mkdir(parents=True)
        (reproduction / "payloads" / "gmail" / "order.eml").write_text(f"{ORDER}{planted}\n")
        (reproduction / "questions.json").write_text(json.dumps(QUESTIONS))
        (reproduction / "expectation.md").write_text(
            "The assistant should say the order ships Friday; it says it doesn't know.\n"
        )
        return left

    return leave


def refusals(printed: str, rules: tuple[str, ...]) -> list[dict[str, int]]:
    """Each refusal the script printed: every rule's count, by its name."""
    passes = printed.split("refused, and nothing is released\n")[1:]
    return [
        {
            rule: int(line.removeprefix(f"check {rule}: "))
            for rule in rules
            for line in refused.splitlines()
            if line.startswith(f"check {rule}: ")
        }
        for refused in passes
    ]


def test_a_name_an_address_a_quote_and_a_number_planted_in_what_a_session_leaves_are_refused(
    tmp_path: Path,
    data_root: DataRoot,
    parts: Any,
    leaves_a_reproduction: Callable[..., Path],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    planted = f"{NAMED} wrote from {ADDRESS}: {SENTENCE}. Quote {NUMBER} when you call."

    # A scripted session leaves the same output each time it revises.
    result = diagnose_sh(
        *parts.arguments(), *("--agent", "scripted", "--leaves", leaves_a_reproduction(planted))
    )

    assert result.returncode == REFUSED, result.stderr
    # Each is refused under its own rule: once in the findings and once in the reproduction,
    # on the first pass and on both revisions.
    found = {"names": 2, "addresses": 2, "quotes": 2, "digits": 2}
    assert refusals(result.stdout, rules) == [{**dict.fromkeys(rules, 0), **found}] * 3
    assert not (tmp_path / "released").exists() or not list((tmp_path / "released").iterdir())
    # Nothing the script prints holds any of them. The owner's notes hold them all, with where.
    printed = (result.stdout + result.stderr).lower()
    for leak in PLANTED:
        assert leak.lower() not in printed
    [private] = data_root.diagnoses.iterdir()
    notes = (private / "check-notes.md").read_text()
    for where in ("findings.json: f1.mechanism", "reproductions/f1/payloads/gmail/order.eml:9"):
        assert f"- {where}: the name {NAMED.lower()!r}" in notes
        assert f"- {where}: the address {ADDRESS!r}" in notes
        assert f"- {where}: 8 words in a row that the case holds" in notes
        assert f"- {where}: a number of 10 digits: {NUMBER!r}" in notes
    assert modes(private) == PRIVATE


def test_a_file_that_isnt_allowed_in_what_comes_back_from_the_vm_is_refused(
    tmp_path: Path,
    data_root: DataRoot,
    parts: Any,
    leaves_a_reproduction: Callable[..., Path],
    rules: tuple[str, ...],
    diagnose_sh: Callable[..., subprocess.CompletedProcess[str]],
):
    # `pa-eval diagnose` holds a session's output to its form where the session ran, and the
    # check on the devbox never counts on that: these arrive after it, in the findings' place
    # and in a reproduction.
    added = tmp_path / "added"
    for place in ("output", "output/reproductions/f1/payloads/gmail"):
        (added / place).mkdir(parents=True)
        (added / place / "gooseberry.txt").write_text(f"{NAMED}, {ADDRESS}\n")

    result = diagnose_sh(
        *parts.arguments(),
        *("--agent", "scripted", "--leaves", leaves_a_reproduction()),
        vm_adds=added,
    )

    assert result.returncode == REFUSED, result.stderr
    assert refusals(result.stdout, rules) == [{**dict.fromkeys(rules, 0), "shape": 2}] * 3
    assert not (tmp_path / "released").exists() or not list((tmp_path / "released").iterdir())
    printed = (result.stdout + result.stderr).lower()
    for leak in (NAMED, ADDRESS, "gooseberry"):
        assert leak.lower() not in printed
    [private] = data_root.diagnoses.iterdir()
    notes = (private / "check-notes.md").read_text()
    assert "- output/gooseberry.txt: an unexpected file" in notes
    assert "- output/reproductions/f1/payloads/gmail/gooseberry.txt: an unexpected file" in notes
