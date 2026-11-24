"""diagnose.sh: one command on the devbox runs a whole diagnosis through the assistant's VM
(ADR-0010). The case is pushed in, a diagnostician's session answers the referral there, what it
left comes back under the data root, and the release check runs on the devbox.

It runs here on stand-ins that record each call: `incus` (and `sudo`), which also plays the VM
(its directories, the session's command, and what a pull brings back); the devbox's `pa-eval`,
which exports a made-up history and answers as a release would; and `systemctl`, for the nightly
job's unit. Everything in the case is made up.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pa_evals import diagnosis, release_cli
from pa_evals.diagnose_cli import add_diagnose_command
from pa_evals.diagnosis import NAME
from pa_evals.history_cli import add_wiki_history_command
from pa_evals.release_cli import add_release_command
from pa_home import config
from pa_home.config import DataRoot

DEPLOY = Path(__file__).resolve().parents[1]
DIAGNOSE = DEPLOY / "diagnose.sh"
LIB = DEPLOY / "lib.sh"

VM_CUTS = "/srv/pa/eval/cuts"
VM_CASE = "/srv/pa/eval/case"
VM_DIAGNOSES = "/srv/pa/eval/diagnoses"
VM_EVAL_TMP = "/srv/pa/eval/tmp"
CUT_NAME = "20261026T120000Z"
NAMED = r"\d{8}T\d{6}Z-dreamed-drop"
RULES = (
    "shape",
    "schema",
    "paths",
    "episodes",
    "questions",
    "labels",
    "names",
    "addresses",
    "quotes",
    "digits",
    "reproductions",
    "one-word-names",
    "owner",
    "encodings",
    "ingest",
    "lint",
)

STAND_IN_INCUS = """\
import json, os, shlex, sys
calls = os.environ["CALLS"]
number = len(os.listdir(calls))
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" else b""
with open(os.path.join(calls, f"{number:03}.json"), "w") as call:
    json.dump({"prog": "incus", "argv": argv, "stdin": stdin.hex()}, call)
# The VM's directories, as far as the script can learn or change them: a cut a push moved into
# place (`... && mv -T PARTIAL DIR`) and a diagnosis a session left, kept in a file between calls.
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
    # How this session ends: the first of the ways given when it is the first to run, the next
    # for the one after it, and the last again after that.
    earlier = [json.load(open(os.path.join(calls, file))) for file in sorted(os.listdir(calls))]
    ran = [c for c in earlier if c["prog"] == "incus" and c["argv"][-1].startswith("bash -s")]
    ways = json.load(open(os.environ["SESSION"]))
    session = ways[min(len(ran) - 1, len(ways) - 1)]
    name = words[words.index("--name") + 1]
    diagnosis = words[words.index("--out") + 1] + "/" + name
    # A revision revises the diagnosis where it is, and keeps the pass it revised by number.
    revision = len([c for c in ran if "--revise" in shlex.split(c["argv"][-1])])
    if session["leaves"]:
        with open(vm_dirs, "a") as listed:
            listed.write(diagnosis + (f"/refused/{revision}" if revision else "") + "\\n")
    for path in session["touches"]:
        open(path, "w").write("{}")
    said = session["stdout"].replace("{name}", name).replace("{revisions}", str(revision))
    sys.stdout.write(said)
    sys.stderr.write(session["stderr"].replace("{name}", name))
    sys.exit(session["exit"])
if argv[:3] == ["file", "pull", "-r"]:
    # What a session leaves, as a pull brings it back: readable by everyone until tightened.
    # With it comes each pass a revision kept there.
    source = argv[-2].split("/", 1)[1]
    target = os.path.join(argv[-1], os.path.basename(source))
    left = ["referral.md", "reply.md", "session.json", "output/analysis.md", "output/findings.json"]
    earlier = [json.load(open(os.path.join(calls, file))) for file in sorted(os.listdir(calls))]
    in_vm = [c["argv"][-1] if c["prog"] == "incus" else "" for c in earlier]
    # The referral comes back as it was pushed, unless the last session's way says otherwise.
    [pushed] = [c for c, ran in zip(earlier, in_vm) if ran.endswith("/case/referral.md")][-1:]
    ways = json.load(open(os.environ["SESSION"]))
    way = ways[min(len([ran for ran in in_vm if ran.startswith("bash -s")]), len(ways)) - 1]
    comes_back = {"referral.md": bytes.fromhex(pushed["stdin"]).decode(), **way["comes_back"]}
    for kept in known:
        if kept.startswith("/" + source + "/refused/"):
            number = os.path.basename(kept)
            left += [f"refused/{number}/output/findings.json", f"refused/{number}/check-notes.md"]
    for file in left:
        os.makedirs(os.path.dirname(os.path.join(target, file)), exist_ok=True)
        with open(os.path.join(target, file), "w") as pulled:
            pulled.write(comes_back.get(file, "made up\\n"))
        os.chmod(os.path.join(target, file), 0o644)
    # A link the session left in its diagnosis, as a pull brings one back.
    for file, to in way["links"].items():
        if os.path.lexists(os.path.join(target, file)):
            os.remove(os.path.join(target, file))
        os.symlink(to, os.path.join(target, file))
    for directory, _, _ in os.walk(target):
        os.chmod(directory, 0o755)
"""

STAND_IN_PA_EVAL = """\
import json, os, sys
calls = os.environ["CALLS"]
number = len(os.listdir(calls))
argv = sys.argv[1:]
option = lambda name: [argv[i + 1] for i, word in enumerate(argv[:-1]) if word == name]
# What the command could read when it ran: each history it was pointed at.
histories = {}
for value in option("--history"):
    label, _, directory = value.partition("=")
    histories[label] = sorted(os.listdir(directory)) if os.path.isdir(directory) else None
referral = [open(path).read() for path in option("--referral")]
with open(os.path.join(calls, f"{number:03}.json"), "w") as call:
    env = {name: value for name, value in os.environ.items() if name.startswith("PA_")}
    took = {"argv": argv, "env": env, "histories": histories, "referral": referral}
    json.dump({"prog": "pa-eval", **took}, call)
if argv[0] == "wiki-history":
    [out] = option("--out")
    os.makedirs(out)
    with open(os.path.join(out, "0001-0123456789ab.patch"), "w") as exported:
        exported.write("commit 0123456789ab\\n")
    print("history: 1 runs")
    print(f"left: {out}")
if argv[0] == "release":
    # How this check ends: the first of the ways given when it is the first to run, the next for
    # the one after it, and the last again after that.
    earlier = [json.load(open(os.path.join(calls, file))) for file in sorted(os.listdir(calls))]
    ran = [c for c in earlier if c["prog"] == "pa-eval" and c["argv"][0] == "release"]
    ways = json.load(open(os.environ["RELEASE"]))
    release = ways[min(len(ran) - 1, len(ways) - 1)]
    [name] = option("--name")
    diagnoses = os.environ["PA_DATA_DIR"] + "/diagnoses"
    if release.get("notes"):
        # A refusal keeps what the check found with the diagnosis.
        with open(os.path.join(diagnoses, name, "check-notes.md"), "w") as notes:
            notes.write(release["notes"].replace("{pass}", str(len(ran))))
    said = release["stdout"].replace("{name}", name).replace("{diagnoses}", diagnoses)
    sys.stdout.write(said.replace("{released}", os.environ["PA_RELEASED_DIR"]))
    sys.stderr.write(release["stderr"])
    sys.exit(release["exit"])
"""

SESSION_SAYS = """\
diagnosis: {name}, over the cut 20261026T120000Z
case: the cut alone
session: $2.64, 77 turns
resumes: 0
revisions: {revisions}
left: /srv/pa/eval/diagnoses/{name}
"""

CHECKS = "".join(f"check {rule}: 0\n" for rule in RULES)

RELEASE_SAYS = f"""\
release: {{name}}: released
released: {{released}}/{{name}}
findings: 2 (1 fault, 1 noise)
reproductions: 1, untested
payloads: 3
{CHECKS}"""


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    directory = tmp_path / "calls"
    directory.mkdir()
    return directory


def data_root(tmp_path: Path) -> Path:
    """The instance's data root, under the stand-in home."""
    return tmp_path / "home" / ".local" / "share" / "pa-home"


def diagnoses(tmp_path: Path) -> list[Path]:
    """Each diagnosis in the private diagnoses directory."""
    directory = data_root(tmp_path) / "diagnoses"
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if not path.name.startswith("."))


def diagnose(
    tmp_path: Path,
    calls: Path,
    *args: str,
    session: dict[str, Any] | list[dict[str, Any]] | None = None,
    release: dict[str, Any] | list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Runs diagnose.sh. `session` is how the session's command in the VM ends, and `release`
    how the devbox's release check does. A session's `touches` are files it writes while it
    runs, its `comes_back` what files of the diagnosis hold when they are pulled, by name, and
    its `links` links it left in the diagnosis, by name, with where each points. A list of
    either is how each one ends in turn: the first pass's, then each revision's (the last
    again, after that)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stand_ins = {
        "incus": f"#!{sys.executable}\n{STAND_IN_INCUS}",
        "pa-eval": f"#!{sys.executable}\n{STAND_IN_PA_EVAL}",
        "sudo": '#!/bin/bash\nexec "$@"\n',
        "systemctl": '#!/bin/bash\necho "${NIGHTLY_UNIT:-inactive}"\n',
    }
    for name, text in stand_ins.items():
        script = bin_dir / name
        script.write_text(text)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    root = data_root(tmp_path)
    (root / "inbox" / "assistant_chat").mkdir(parents=True, exist_ok=True)
    (root / "owner.env").write_text(
        "PA_OWNER_NAME='Argus McNevans'\nPA_OWNER_EMAILS=argus@example.com\n"
    )
    said = {"exit": 0, "stdout": SESSION_SAYS, "stderr": "", "leaves": True, "touches": []}
    said |= {"comes_back": {}, "links": {}}
    sessions_end = session if isinstance(session, list) else [session or {}]
    (tmp_path / "session.json").write_text(json.dumps([said | way for way in sessions_end]))
    checked = {"exit": 0, "stdout": RELEASE_SAYS, "stderr": ""}
    releases_end = release if isinstance(release, list) else [release or {}]
    (tmp_path / "release.json").write_text(json.dumps([checked | way for way in releases_end]))
    environ = {
        "HOME": str(tmp_path / "home"),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(calls),
        "VM_DIRS": str(tmp_path / "vm-dirs"),
        "SESSION": str(tmp_path / "session.json"),
        "RELEASE": str(tmp_path / "release.json"),
        "PA_EVAL_BIN": str(bin_dir / "pa-eval"),
        "PA_RELEASED_DIR": str(tmp_path / "released"),
        **(env or {}),
    }
    return subprocess.run(
        ["bash", str(DIAGNOSE), *args], capture_output=True, text=True, env=environ, check=False
    )


def recorded(calls: Path) -> list[dict]:
    """Every call the stand-ins took, in order: `at` is each one's place."""
    return [
        {"at": at, **json.loads(path.read_text())}
        for at, path in enumerate(sorted(calls.iterdir()))
    ]


def in_vm(call: dict) -> str:
    """The command a call runs in the VM, or nothing for any other call."""
    return call["argv"][-1] if call["prog"] == "incus" and call["argv"][0] == "exec" else ""


def sessions(calls: Path) -> list[dict]:
    """The calls that run the session's command in the VM."""
    return [call for call in recorded(calls) if in_vm(call).startswith("bash -s")]


def releases(calls: Path) -> list[dict]:
    """The devbox's release checks."""
    return [
        call
        for call in recorded(calls)
        if call["prog"] == "pa-eval" and call["argv"][0] == "release"
    ]


def options_of(session: dict) -> list[str]:
    """The `pa-eval diagnose` options of a session's call, which runs from root's code with its
    throwaway directory outside the assistant's home."""
    _bash, _s, _dashes, repository, tmp, *rest = shlex.split(in_vm(session))
    assert (repository, tmp) == ("/opt/pa", VM_EVAL_TMP)
    return rest[len(configuration_of(session)) :]


def configuration_of(session: dict) -> list[str]:
    """The diagnostician's configuration a session's call carries into the VM, ahead of the
    options, as NAME=VALUE."""
    rest = shlex.split(in_vm(session))[5:]
    carried = [word for word in rest if re.match(r"PA_[A-Z_]+=", word)]
    assert rest[: len(carried)] == carried
    return carried


def played(tmp_path: Path, session: dict) -> dict:
    """What the VM's `pa-eval` is run with: the session's script, run as the VM would run it,
    over a stand-in for root's checkout whose `pa-eval` records its arguments and environment."""
    repository = tmp_path / "opt-pa"
    pa_eval = repository / ".venv" / "bin" / "pa-eval"
    pa_eval.parent.mkdir(parents=True)
    record = tmp_path / "vm-pa-eval.json"
    pa_eval.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(record)!r}, 'w') as out:\n"
        "    json.dump({'argv': sys.argv[1:], 'env': dict(os.environ), 'cwd': os.getcwd()}, out)\n"
    )
    pa_eval.chmod(0o755)
    _bash, _s, _dashes, _repository, _tmp, *rest = shlex.split(in_vm(session))
    subprocess.run(
        ["bash", "-s", "--", str(repository), str(tmp_path / "vm-tmp"), *rest],
        input=script_of(session),
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        check=True,
    )
    return json.loads(record.read_text())


def given(options: list[str], option: str) -> list[str]:
    """Each value `option` was given."""
    return [options[i + 1] for i, word in enumerate(options[:-1]) if word == option]


def script_of(call: dict) -> str:
    return bytes.fromhex(call["stdin"]).decode()


def pushed(call: dict) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(bytes.fromhex(call["stdin"]))) as archive:
        return sorted(member.name for member in archive.getmembers() if member.isfile())


def modes(directory: Path) -> set[tuple[str, int]]:
    """Whether each thing under `directory` is a file, with its mode."""
    return {
        ("file" if path.is_file() else "directory", stat.S_IMODE(path.stat().st_mode))
        for path in (directory, *directory.rglob("*"))
    }


@pytest.fixture
def cut(tmp_path: Path) -> Path:
    """A cut under the data root, as `pa cut` lays one out (its contents made up)."""
    path = data_root(tmp_path) / "cuts" / CUT_NAME
    (path / "l0" / "episodes" / "gmail" / "2026-10").mkdir(parents=True)
    (path / "l0" / "episodes" / "gmail" / "2026-10" / "gmail_0123456789abcdef.json").write_text(
        "{}\n"
    )
    (path / "wiki" / "people").mkdir(parents=True)
    (path / "wiki" / "people" / "nell-okafor.md").write_text("# Nell Okafor\n")
    (path / "cut.json").write_text('{"as_of": "2026-10-26T12:00:00Z"}\n')
    return path


@pytest.fixture
def referral(tmp_path: Path) -> Path:
    path = tmp_path / "referral.md"
    path.write_text("Why do two questions fail more often under `dreamed` than under `own`?\n")
    return path


def test_a_diagnosis_runs_in_the_vm_comes_back_private_and_is_released_on_the_devbox(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = diagnose(
        tmp_path, calls, "--referral", str(referral), "--name", "dreamed-drop", "--cut", str(cut)
    )

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    name = private.name
    assert re.fullmatch(NAMED, name)

    made = recorded(calls)
    [session] = sessions(calls)
    [release] = releases(calls)
    [push_cut] = [call for call in made if "mv -T" in in_vm(call)]
    [push_referral] = [call for call in made if f"cat > {VM_CASE}/referral.md" in in_vm(call)]
    [pull] = [call for call in made if call["argv"][:2] == ["file", "pull"]]
    # The case goes in before the session starts, and the check runs on what came back.
    assert push_cut["at"] < push_referral["at"] < session["at"] < pull["at"] < release["at"]

    # The cut and the referral are pushed, and the session is given the VM's copies.
    assert f"{VM_CUTS}/{CUT_NAME}" in in_vm(push_cut)
    assert "./cut.json" in pushed(push_cut)
    assert bytes.fromhex(push_referral["stdin"]) == referral.read_bytes()
    assert session["argv"][:6] == ["exec", "pa-home", "--", "runuser", "-l", "assistant"]
    assert options_of(session) == [
        *("--agent", "claude"),
        *("--referral", f"{VM_CASE}/referral.md"),
        *("--name", name),
        *("--out", VM_DIAGNOSES),
        *("--cut", f"{VM_CUTS}/{CUT_NAME}"),
    ]
    assert ".venv/bin/pa-eval diagnose" in script_of(session)

    # What the session left comes back under the data root, for the owner alone: pulled beside
    # where it goes, among the diagnosis's own files, and moved into place whole.
    assert pull["argv"] == [
        "file",
        "pull",
        "-r",
        f"pa-home{VM_DIAGNOSES}/{name}",
        f"{private.parent}/.work-{name}/pulled/",
    ]
    assert private.parent == data_root(tmp_path) / "diagnoses"
    assert list(private.parent.iterdir()) == [private]
    assert modes(private.parent) == {("directory", 0o700), ("file", 0o600)}
    assert (private / "output" / "analysis.md").is_file()

    # The release check runs on the devbox, over the devbox's copy of the case, as the instance's
    # owner.
    asked = f"{private.parent}/.work-{name}/referral.md"
    assert release["argv"] == ["release", "--name", name, "--referral", asked, "--cut", str(cut)]
    assert release["env"]["PA_DATA_DIR"] == str(data_root(tmp_path))
    # The referral it checks against is the devbox's own, and so is the one kept privately.
    assert release["referral"] == [referral.read_text()]
    assert (private / "referral.md").read_text() == referral.read_text()
    assert release["env"]["PA_OWNER_NAME"] == "Argus McNevans"

    assert result.stdout == (
        f"diagnosis: {name}, over the cut {CUT_NAME}\n"
        "case: the cut alone\n"
        "session: $2.64, 77 turns\n"
        "resumes: 0\n"
        "revisions: 0\n"
        f"left: {private}\n"
        f"release: {name}: released\n"
        f"released: {tmp_path / 'released'}/{name}\n"
        "findings: 2 (1 fault, 1 noise)\n"
        "reproductions: 1, untested\n"
        "payloads: 3\n" + CHECKS
    )


def refer(tmp_path: Path, calls: Path, referral: Path, cut: Path, *more: str, **how: Any):
    """A diagnosis named `dreamed-drop` over the cut, with whatever else is given."""
    return diagnose(
        tmp_path,
        calls,
        *("--referral", str(referral), "--name", "dreamed-drop", "--cut", str(cut)),
        *more,
        **how,
    )


def test_a_second_diagnosis_reuses_the_vms_cut_and_pushes_its_own_referral(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    first = refer(tmp_path, calls, referral, cut)
    referral.write_text("Was each change the last dreaming run applied right?\n")
    second = diagnose(
        tmp_path, calls, "--referral", str(referral), "--name", "applied", "--cut", str(cut)
    )

    assert (first.returncode, second.returncode) == (0, 0), first.stderr + second.stderr
    made = recorded(calls)
    assert len([call for call in made if "mv -T" in in_vm(call)]) == 1
    assert "pushed the cut" in first.stderr
    assert "pushed the cut" not in second.stderr
    assert f"reusing {VM_CUTS}/{CUT_NAME}" in second.stderr
    # The rest of the case is replaced each time.
    clears = [call for call in made if f"rm -rf {VM_CASE} " in in_vm(call)]
    referrals = [call for call in made if f"cat > {VM_CASE}/referral.md" in in_vm(call)]
    assert len(clears) == 2
    asked_first, asked_second = sessions(calls)
    assert clears[0]["at"] < referrals[0]["at"] < asked_first["at"]
    assert asked_first["at"] < clears[1]["at"] < referrals[1]["at"] < asked_second["at"]
    assert bytes.fromhex(referrals[1]["stdin"]) == referral.read_bytes()
    assert given(options_of(asked_second), "--cut") == [f"{VM_CUTS}/{CUT_NAME}"]
    assert given(options_of(asked_first), "--name") != given(options_of(asked_second), "--name")
    assert len(diagnoses(tmp_path)) == 2


def unpacked_into(calls: Path, vm_directory: str) -> list[str]:
    """The files of the one push unpacked into `vm_directory`."""
    [push] = [
        call for call in recorded(calls) if in_vm(call).endswith(f"tar -C {vm_directory} -xf -")
    ]
    return pushed(push)


def written_to(calls: Path, vm_file: str) -> bytes:
    """What the one push of a file to `vm_file` wrote."""
    [push] = [call for call in recorded(calls) if in_vm(call).endswith(f"cat > {vm_file}")]
    return bytes.fromhex(push["stdin"])


@pytest.fixture
def parts(tmp_path: Path, cut: Path) -> dict[str, Path]:
    """The rest of a case, under the data root: an eval set, three run directories, a dreamed
    copy of the cut's wiki, and an earlier diagnosis."""
    root = data_root(tmp_path)
    eval_set = root / "evals" / "real-set.json"
    eval_set.parent.mkdir(parents=True)
    eval_set.write_text('{"questions": []}\n')
    # A run directory holds one run, or a run-<n>/ for each, and more than its results.
    own = root / "evals" / "runs" / "2026-10-27T090000Z"
    (own / "transcripts").mkdir(parents=True)
    for file in ("results.json", "report.md", "transcripts/r07.jsonl"):
        (own / file).write_text("{}\n")
    several = []
    for directory in ("2026-10-28T090000Z", "2026-10-29T090000Z"):
        several.append(root / "evals" / "runs" / directory)
        for run in ("run-1", "run-2"):
            (several[-1] / run).mkdir(parents=True)
            (several[-1] / run / "results.json").write_text("{}\n")
            (several[-1] / run / "report.md").write_text("made up\n")
    copy = root / "replays" / "dreamed-copy"
    (copy / "wiki" / "people").mkdir(parents=True)
    (copy / "wiki" / "people" / "nell-okafor.md").write_text("# Nell Okafor\n")
    (copy / "wiki.git").mkdir()
    (copy / "wiki.git" / "HEAD").write_text("ref: refs/heads/main\n")
    (copy / "run-log.jsonl").write_text("{}\n")
    earlier = root / "diagnoses" / "20261101T080000Z-first-look"
    (earlier / "output").mkdir(parents=True)
    for file in ("referral.md", "session.json", "output/analysis.md", "output/findings.json"):
        (earlier / file).write_text("made up\n")
    return {
        "eval_set": eval_set,
        "own": own,
        "first": several[0],
        "second": several[1],
        "copy": copy,
        "earlier": earlier,
    }


def whole_case(cut: Path, parts: dict[str, Path]) -> list[str]:
    """The options that name every part of a case."""
    return [
        *("--eval-set", str(parts["eval_set"])),
        *("--runs", f"own={parts['own']}"),
        *("--runs", f"dreamed={parts['first']}", f"--runs=dreamed={parts['second']}"),
        *("--wiki", f"own={cut}", "--wiki", f"dreamed={parts['copy']}"),
        *("--earlier", str(parts["earlier"])),
    ]


def test_the_rest_of_the_case_is_pushed_each_time_with_each_wikis_history_exported_first(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, parts: dict[str, Path]
):
    result = refer(tmp_path, calls, referral, cut, *whole_case(cut, parts))

    assert result.returncode == 0, result.stderr
    made = recorded(calls)
    [session] = sessions(calls)
    [release] = releases(calls)

    # Each wiki's history is exported on the devbox, from the git directory its wiki has there,
    # before anything goes into the VM.
    exports = [call for call in made if call["argv"][0] == "wiki-history"]
    own_history, dreamed_history = (given(call["argv"], "--out")[0] for call in exports)
    assert [call["argv"][1:3] for call in exports] == [
        ["--cut", str(cut)],
        ["--copy", str(parts["copy"])],
    ]
    assert {call["env"]["PA_DATA_DIR"] for call in exports} == {str(data_root(tmp_path))}
    assert all(export["at"] < call["at"] for export in exports for call in made if in_vm(call))

    # Each part is pushed, and no more of it than a case holds.
    assert written_to(calls, f"{VM_CASE}/eval-set.json") == parts["eval_set"].read_bytes()
    assert unpacked_into(calls, f"{VM_CASE}/runs/own/1") == ["./results.json"]
    for number in ("1", "2"):
        assert unpacked_into(calls, f"{VM_CASE}/runs/dreamed/{number}") == [
            "./run-1/results.json",
            "./run-2/results.json",
        ]
    assert unpacked_into(calls, f"{VM_CASE}/wikis/dreamed") == ["wiki/people/nell-okafor.md"]
    for label in ("own", "dreamed"):
        assert unpacked_into(calls, f"{VM_CASE}/wikis/{label}/history") == [
            "./0001-0123456789ab.patch"
        ]
    earlier = parts["earlier"].name
    assert unpacked_into(calls, f"{VM_CASE}/earlier/{earlier}") == ["output/analysis.md"]
    # The cut's own wiki is in the VM already, in the cut.
    assert not [call for call in made if in_vm(call).endswith(f"tar -C {VM_CASE}/wikis/own -xf -")]
    assert all(call["at"] < session["at"] for call in made if "tar -C" in in_vm(call))

    # The session is given the VM's copies.
    assert options_of(session)[8:] == [
        *("--cut", f"{VM_CUTS}/{CUT_NAME}"),
        *("--eval-set", f"{VM_CASE}/eval-set.json"),
        *("--runs", f"own={VM_CASE}/runs/own/1"),
        *("--runs", f"dreamed={VM_CASE}/runs/dreamed/1"),
        *("--runs", f"dreamed={VM_CASE}/runs/dreamed/2"),
        *("--wiki", f"own={VM_CUTS}/{CUT_NAME}/wiki"),
        *("--history", f"own={VM_CASE}/wikis/own/history"),
        *("--wiki", f"dreamed={VM_CASE}/wikis/dreamed/wiki"),
        *("--history", f"dreamed={VM_CASE}/wikis/dreamed/history"),
        *("--earlier", f"{VM_CASE}/earlier/{earlier}"),
    ]

    # The release check is given the devbox's copies of the same parts, in the same order.
    [name] = given(release["argv"], "--name")
    assert release["argv"] == [
        *("release", "--name", name),
        *("--referral", f"{data_root(tmp_path)}/diagnoses/.work-{name}/referral.md"),
        *("--cut", str(cut)),
        *("--eval-set", str(parts["eval_set"])),
        *("--runs", f"own={parts['own']}"),
        *("--runs", f"dreamed={parts['first']}"),
        *("--runs", f"dreamed={parts['second']}"),
        *("--wiki", f"own={cut}/wiki"),
        *("--history", f"own={own_history}"),
        *("--wiki", f"dreamed={parts['copy']}/wiki"),
        *("--history", f"dreamed={dreamed_history}"),
        *("--earlier", str(parts["earlier"])),
    ]
    exported = ["0001-0123456789ab.patch"]
    assert release["histories"] == {"own": exported, "dreamed": exported}
    # The exports are this diagnosis's own, and go when it ends.
    assert not Path(own_history).exists()
    assert sorted(path.name for path in (data_root(tmp_path) / "diagnoses").iterdir()) == [
        earlier,
        name,
    ]


def taken(argv: list[str]) -> argparse.Namespace:
    """`argv` as the real `pa-eval` reads it. An option the command doesn't have ends the test."""
    parser = argparse.ArgumentParser(prog="pa-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    add_wiki_history_command(commands)
    add_diagnose_command(commands)
    add_release_command(commands)
    return parser.parse_args(argv)


def test_every_option_it_passes_is_one_the_commands_it_runs_take(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, parts: dict[str, Path]
):
    """The stand-ins here take anything; `pa-eval` doesn't."""
    result = refer(tmp_path, calls, referral, cut, *whole_case(cut, parts), "--max-turns", "150")

    assert result.returncode == 0, result.stderr
    [session] = sessions(calls)
    [release] = releases(calls)
    exports = [call for call in recorded(calls) if call["argv"][0] == "wiki-history"]
    in_vm_as, on_devbox_as = taken(["diagnose", *options_of(session)]), taken(release["argv"])
    assert (in_vm_as.agent, in_vm_as.max_turns) == ("claude", 150)
    assert in_vm_as.name == on_devbox_as.name
    for told in (in_vm_as, on_devbox_as):
        assert told.eval_set is not None
        assert [value.split("=")[0] for value in told.runs] == ["own", "dreamed", "dreamed"]
        assert [value.split("=")[0] for value in told.wiki] == ["own", "dreamed"]
        assert [value.split("=")[0] for value in told.history] == ["own", "dreamed"]
        assert [path.name for path in told.earlier] == [parts["earlier"].name]
    assert [taken(call["argv"]).copy for call in exports] == [None, parts["copy"]]


def test_the_session_runs_as_the_assistant_with_capture_off_and_the_devboxs_configuration(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """Exchange capture is on only where PA_CAPTURE_EXCHANGES is set, which is the Remote
    Control unit alone: the session's command starts from the assistant's login environment, and
    nothing here sets the variable."""
    configuration = {
        "PA_DIAGNOSTICIAN_MODEL": "claude-made-up-1",
        "PA_DIAGNOSTICIAN_TIMEOUT_SECONDS": "7200",
    }
    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        *("--effort", "max"),
        env={**configuration, "PA_CAPTURE_EXCHANGES": "1"},
    )

    assert result.returncode == 0, result.stderr
    [session] = sessions(calls)
    assert session["argv"][:7] == ["exec", "pa-home", "--", "runuser", "-l", "assistant", "-c"]
    assert "PA_CAPTURE_EXCHANGES" not in " ".join(session["argv"]) + script_of(session)
    assert options_of(session)[:4] == ["--agent", "claude", "--effort", "max"]

    ran = played(tmp_path, session)
    assert ran["argv"] == ["diagnose", *options_of(session)]
    assert ran["cwd"] == str(tmp_path / "opt-pa")
    # The session's throwaway copy of the case goes on the VM's disk, outside the assistant's home.
    assert ran["env"]["TMPDIR"] == str(tmp_path / "vm-tmp")
    assert (tmp_path / "vm-tmp").is_dir()
    # The model, effort, turn limit, and timeout are the devbox's to set.
    assert {name: ran["env"].get(name) for name in configuration} == configuration
    assert "PA_DIAGNOSTICIAN_EFFORT" not in ran["env"]
    assert "PA_CAPTURE_EXCHANGES" not in ran["env"]


PRIVATE = {("directory", 0o700), ("file", 0o600)}


def test_a_chat_inbox_that_changed_during_the_session_fails_the_diagnosis(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """Capture is off in the session, so nothing it says comes back into L0: an inbox that
    changed while it ran fails the diagnosis, and nothing is checked or released."""
    inbox = data_root(tmp_path) / "inbox" / "assistant_chat"

    result = refer(
        tmp_path, calls, referral, cut, session={"touches": [str(inbox / "exchange.json")]}
    )

    assert result.returncode == 1
    assert f"changed {inbox}" in result.stderr
    assert releases(calls) == []
    assert "release" not in result.stdout
    # What the session left is still the owner's to read.
    [private] = diagnoses(tmp_path)
    assert modes(private) == PRIVATE
    assert f"left: {private}\n" in result.stdout


REFUSED = 3
FAILED = 4

REFUSED_CHECKS = CHECKS.replace("names: 0", "names: 2").replace("quotes: 0", "quotes: 1")
REFUSAL_SAYS = (
    "release: {name}: refused, and nothing is released\n"
    + REFUSED_CHECKS
    + "notes: {diagnoses}/{name}/check-notes.md\n"
)
NOTES = """\
# The release check's notes, on pass {pass}

## names: 2

- findings.json: f1.mechanism: the name 'nell okafor', which the case holds (Nell Okafor)
- findings.json: summary: the name 'ridgeline lettings', which the case holds (Ridgeline)

## quotes: 1

- findings.json: f1.suggestion: 6 words in a row: 'the lease at Ridgeline renews in'
"""
"""What a refusal's notes hold: what the check found, in words of the made-up case (`SALT`)."""
REFUSAL = {"exit": REFUSED, "stdout": REFUSAL_SAYS, "notes": NOTES}

VM_REFUSED = f"{VM_CASE}/refused"


CHANGED = "Why did Nell Okafor ask Ridgeline about a lease that renews?\n"
"""A referral as a session in the VM might leave it, to have the check excuse what it holds."""


def test_a_referral_that_comes_back_changed_fails_the_diagnosis_with_nothing_checked(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """The check excuses what the referral holds, so the referral is the devbox's to say: the
    copy the VM kept with the diagnosis is the session's to change."""
    result = refer(tmp_path, calls, referral, cut, session={"comes_back": {"referral.md": CHANGED}})

    assert result.returncode == 1
    assert "isn't the referral that was pushed" in result.stderr
    assert releases(calls) == []
    assert "release" not in result.stdout
    assert not (tmp_path / "released").exists()
    # The devbox's own copy is the one kept; what came back is beside it, for the owner.
    [private] = diagnoses(tmp_path)
    assert (private / "referral.md").read_text() == referral.read_text()
    assert (private / "referral-from-the-vm.md").read_text() == CHANGED
    assert modes(private) == PRIVATE
    assert not [word for word in SALT if word in result.stdout + result.stderr]


def test_a_referral_that_comes_back_changed_from_a_revision_fails_the_diagnosis_too(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session=[{}, {"comes_back": {"referral.md": CHANGED}}],
        release=[REFUSAL, {}],
    )

    assert result.returncode == 1
    assert "isn't the referral that was pushed" in result.stderr
    # The first pass was checked and refused. The revision never was.
    assert len(sessions(calls)) == 2
    assert len(releases(calls)) == 1
    assert not (tmp_path / "released").exists()
    [private] = diagnoses(tmp_path)
    assert (private / "referral.md").read_text() == referral.read_text()
    assert (private / "referral-from-the-vm.md").read_text() == CHANGED


def test_every_pass_is_checked_against_the_referral_as_it_was_when_the_diagnosis_started(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    asked = referral.read_text()

    # The file is written over while the first session runs.
    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"touches": [str(referral)]},
        release=[REFUSAL, {}],
    )

    assert result.returncode == 0, result.stderr
    assert referral.read_text() != asked
    assert [release["referral"] for release in releases(calls)] == [[asked], [asked]]
    [private] = diagnoses(tmp_path)
    assert (private / "referral.md").read_text() == asked


@pytest.mark.parametrize("link", ["referral.md", "release-output.txt", "diagnose-output.txt"])
def test_a_link_that_comes_back_in_a_diagnosis_is_never_written_through(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, link: str
):
    """The script writes the referral and what its commands let slip into the private
    directory, by name. A session that left a link of one of those names would have it write
    wherever the link points on the devbox."""
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("the developer's own file\n")

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"links": {link: str(elsewhere)}, "stderr": SLIPPED},
        release={"exit": 1, "stdout": "", "stderr": SLIPPED},
    )

    assert result.returncode == 1
    assert elsewhere.read_text() == "the developer's own file\n"
    [private] = diagnoses(tmp_path)
    assert not (private / link).is_symlink()
    assert (private / "referral.md").read_text() == referral.read_text()


def session_says(name: str, private: Path, *, revisions: int) -> list[str]:
    """What the script prints of a session that ended well, and of bringing back what it left."""
    return [
        f"diagnosis: {name}, over the cut {CUT_NAME}",
        "case: the cut alone",
        "session: $2.64, 77 turns",
        "resumes: 0",
        f"revisions: {revisions}",
        f"left: {private}",
    ]


def refusal_says(name: str, private: Path) -> list[str]:
    return [
        f"release: {name}: refused, and nothing is released",
        *REFUSED_CHECKS.splitlines(),
        f"notes: {private}/check-notes.md",
    ]


def pushes_of_the_refused_pass(calls: Path) -> list[tuple[dict, dict]]:
    """Each revision's two pushes into the VM: the refused output, and the check's notes."""
    made = recorded(calls)
    outputs = [call for call in made if in_vm(call).endswith(f"tar -C {VM_REFUSED} -xf -")]
    notes = [call for call in made if in_vm(call).endswith(f"cat > {VM_REFUSED}/check-notes.md")]
    assert len(outputs) == len(notes)
    return list(zip(outputs, notes, strict=True))


def test_a_refused_diagnosis_is_revised_by_the_checks_notes_and_released_once_the_check_passes(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = refer(tmp_path, calls, referral, cut, release=[REFUSAL, {}])

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    name = private.name
    # One revision: a second session, and the check run again on what it left.
    first, revision = sessions(calls)
    refusal, release = releases(calls)
    [(push_output, push_notes)] = pushes_of_the_refused_pass(calls)
    pulls = [call for call in recorded(calls) if call["argv"][:2] == ["file", "pull"]]
    assert [call["at"] for call in (first, pulls[0], refusal, push_output, push_notes)] == sorted(
        call["at"] for call in (first, pulls[0], refusal, push_output, push_notes)
    )
    assert push_notes["at"] < revision["at"] < pulls[1]["at"] < release["at"]
    assert len(pulls) == 2

    # The refused output and the check's notes go into the VM, which may hold them, on stdin.
    assert pushed(push_output) == ["output/analysis.md", "output/findings.json"]
    assert bytes.fromhex(push_notes["stdin"]).decode() == NOTES.replace("{pass}", "1")
    # The revision is asked the same referral over the same case, with the VM's copies.
    assert options_of(revision) == [
        *options_of(first),
        *("--revise", f"{VM_REFUSED}/output"),
        *("--notes", f"{VM_REFUSED}/check-notes.md"),
    ]
    revised_as = taken(["diagnose", *options_of(revision)])
    assert (str(revised_as.revise), str(revised_as.notes)) == (
        f"{VM_REFUSED}/output",
        f"{VM_REFUSED}/check-notes.md",
    )
    assert revision["argv"][:6] == ["exec", "pa-home", "--", "runuser", "-l", "assistant"]
    # The check is the same one, over the same case: a retry loosens nothing.
    assert release["argv"] == refusal["argv"]
    assert release["env"]["PA_OWNER_NAME"] == "Argus McNevans"

    # What comes back is the diagnosis with every pass in it, for the owner alone.
    assert (private / "refused" / "1" / "check-notes.md").is_file()
    assert (private / "output" / "findings.json").is_file()
    assert modes(private) == PRIVATE
    assert [path.name for path in private.parent.iterdir()] == [name]

    # It prints each pass's counts, and how many revisions were made: never the notes.
    assert result.stdout.splitlines() == [
        *session_says(name, private, revisions=0),
        *refusal_says(name, private),
        *session_says(name, private, revisions=1),
        f"release: {name}: released",
        f"released: {tmp_path / 'released'}/{name}",
        "findings: 2 (1 fault, 1 noise)",
        "reproductions: 1, untested",
        "payloads: 3",
        *CHECKS.splitlines(),
    ]
    assert "revising the diagnosis" in result.stderr
    assert not [word for word in SALT if word in result.stdout + result.stderr]
    assert not [call for call in recorded(calls) if "Okafor" in " ".join(call["argv"])]


def test_a_diagnosis_refused_three_times_ends_unreleased_after_exactly_two_revisions(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = refer(tmp_path, calls, referral, cut, release=REFUSAL)

    assert result.returncode == REFUSED
    [private] = diagnoses(tmp_path)
    name = private.name
    # The first pass and two revisions, each one checked, and no more.
    assert ["--revise" in options_of(session) for session in sessions(calls)] == [False, True, True]
    assert len(releases(calls)) == 3
    assert len({json.dumps(release["argv"]) for release in releases(calls)}) == 1
    # Each revision is given the notes of the check that refused the pass before it.
    told = [
        bytes.fromhex(notes["stdin"]).decode() for _, notes in pushes_of_the_refused_pass(calls)
    ]
    assert told == [NOTES.replace("{pass}", "1"), NOTES.replace("{pass}", "2")]
    assert result.stdout.splitlines() == [
        *session_says(name, private, revisions=0),
        *refusal_says(name, private),
        *session_says(name, private, revisions=1),
        *refusal_says(name, private),
        *session_says(name, private, revisions=2),
        *refusal_says(name, private),
    ]
    assert (
        f"error: the diagnosis {name} is still refused after 2 revisions, and nothing is released"
        in result.stderr
    )
    assert not (tmp_path / "released").exists()
    # Every pass stays with the private analysis, the last check's notes among them.
    assert sorted(path.name for path in (private / "refused").iterdir()) == ["1", "2"]
    assert (private / "check-notes.md").read_text() == NOTES.replace("{pass}", "3")
    assert modes(private) == PRIVATE
    assert not [word for word in SALT if word in result.stdout + result.stderr]


def test_a_revision_whose_session_fails_ends_the_diagnosis_with_the_failed_code(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    failure = (
        "pa-eval: the diagnostician's session ran out of time, so the diagnosis {name} failed. "
        "What it left, and the whole error, are kept in /srv/pa/eval/diagnoses/{name}\n"
    )

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session=[{}, {"exit": 1, "stdout": "", "stderr": failure}],
        release=REFUSAL,
    )

    assert result.returncode == FAILED
    # What the failed revision left isn't checked, so the refusal stands and nothing is released.
    assert len(sessions(calls)) == 2
    assert len(releases(calls)) == 1
    [private] = diagnoses(tmp_path)
    assert result.stdout.splitlines() == [
        *session_says(private.name, private, revisions=0),
        *refusal_says(private.name, private),
        f"left: {private}",
    ]
    assert "ran out of time" in result.stderr
    # The refused pass and what the revision left are the owner's to read.
    assert (private / "refused" / "1" / "check-notes.md").is_file()
    assert modes(private) == PRIVATE


def test_a_revision_that_never_started_leaves_the_refused_diagnosis_as_it_was(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """A revision that fails before it touches the diagnosis left nothing to bring back: the
    devbox's copy, with the check's notes, isn't replaced by the VM's."""
    unstarted = "pa-eval: the diagnosis read another case than the one given\n"

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session=[{}, {"exit": 1, "stdout": "", "stderr": unstarted, "leaves": False}],
        release=REFUSAL,
    )

    assert result.returncode == 1
    assert "read another case" in result.stderr
    assert "started no session" in result.stderr
    assert len([call for call in recorded(calls) if call["argv"][:2] == ["file", "pull"]]) == 1
    assert len(releases(calls)) == 1
    [private] = diagnoses(tmp_path)
    assert (private / "check-notes.md").read_text() == NOTES.replace("{pass}", "1")
    assert not (private / "refused").exists()
    assert modes(private) == PRIVATE


def test_a_chat_inbox_that_changed_during_a_revision_fails_the_diagnosis(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    inbox = data_root(tmp_path) / "inbox" / "assistant_chat"

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session=[{}, {"touches": [str(inbox / "exchange.json")]}],
        release=REFUSAL,
    )

    assert result.returncode == 1
    assert f"changed {inbox}" in result.stderr
    # The revision isn't checked, so it can't be released.
    assert len(sessions(calls)) == 2
    assert len(releases(calls)) == 1


def test_what_each_pass_let_slip_is_kept_for_the_owner_through_a_revision(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """The VM's copy of a revised diagnosis takes the place of the devbox's, which alone holds
    what the commands printed and wasn't shown."""
    stray = "The lease at Ridgeline renews in March.\n"

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"stdout": stray + SESSION_SAYS},
        release=[REFUSAL | {"stderr": SLIPPED}, {}],
    )

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    assert (private / "diagnose-output.txt").read_text() == stray + stray
    assert (private / "release-output.txt").read_text() == SLIPPED
    assert modes(private) == PRIVATE
    assert not [word for word in SALT if word in result.stdout + result.stderr]


def test_a_failed_session_exits_with_a_code_of_its_own_and_what_it_left_comes_back(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    failure = (
        "pa-eval: the diagnostician's session ran out of time, so the diagnosis {name} failed. "
        "What it left, and the whole error, are kept in /srv/pa/eval/diagnoses/{name}\n"
    )

    result = refer(
        tmp_path, calls, referral, cut, session={"exit": 1, "stdout": "", "stderr": failure}
    )

    assert result.returncode == FAILED
    # Nothing is checked, so nothing can be released; what it left is the owner's to read.
    assert releases(calls) == []
    [private] = diagnoses(tmp_path)
    assert modes(private) == PRIVATE
    assert result.stdout == f"left: {private}\n"
    assert "ran out of time" in result.stderr


def test_a_diagnosis_whose_session_never_started_exits_as_any_error_does(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    refusal = "pa-eval: a run under own wasn't asked over the cut 20261026T120000Z\n"

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"exit": 1, "stdout": "", "stderr": refusal, "leaves": False},
    )

    assert result.returncode == 1
    assert "wasn't asked over the cut" in result.stderr
    assert not [call for call in recorded(calls) if call["argv"][:2] == ["file", "pull"]]
    assert releases(calls) == []
    assert diagnoses(tmp_path) == []
    assert result.stdout == ""


@pytest.mark.parametrize("code", [1, 2, FAILED])
def test_a_release_check_that_couldnt_run_exits_as_any_error_does(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, code: int
):
    said = "pa-eval: PA_OWNER_NAME isn't set\n"

    result = refer(
        tmp_path, calls, referral, cut, release={"exit": code, "stdout": "", "stderr": said}
    )

    assert result.returncode == 1
    assert "PA_OWNER_NAME isn't set" in result.stderr
    # The diagnosis is kept, for a check run by hand.
    [private] = diagnoses(tmp_path)
    assert modes(private) == PRIVATE


def mark_nightly(tmp_path: Path, since: datetime | None) -> None:
    """Leaves the status as the nightly job does while it runs: marked as running since `since`
    (or, with None, as not running)."""
    status = data_root(tmp_path) / "status" / "status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    running = None if since is None else since.isoformat()
    status.write_text(json.dumps({"nightly": {"running_since": running}}))


def test_it_refuses_to_start_while_the_nightly_jobs_unit_runs(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, parts: dict[str, Path]
):
    """A diagnosis spends the usage limit the librarian does. The unit is a oneshot, which is
    `activating` for as long as its job runs."""
    result = refer(
        tmp_path, calls, referral, cut, *whole_case(cut, parts), env={"NIGHTLY_UNIT": "activating"}
    )

    assert result.returncode == 1
    assert "the nightly job is running" in result.stderr
    assert recorded(calls) == []
    assert [path.name for path in diagnoses(tmp_path)] == [parts["earlier"].name]


def test_it_refuses_to_start_while_the_status_says_the_nightly_job_runs(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """A nightly job run by hand isn't the unit, and leaves the same mark in the status."""
    mark_nightly(tmp_path, datetime.now(UTC) - timedelta(minutes=10))

    result = refer(tmp_path, calls, referral, cut)

    assert result.returncode == 1
    assert "the nightly job is running" in result.stderr
    assert recorded(calls) == []
    assert diagnoses(tmp_path) == []


@pytest.mark.parametrize("since", [None, timedelta(hours=5)], ids=["ended", "cut-short"])
def test_a_nightly_job_that_ended_or_was_cut_short_doesnt_stop_a_diagnosis(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, since: timedelta | None
):
    """The mark is cleared when the job ends, and one left by a job that was killed stops
    counting once it is older than any night runs (pa_core.status.NIGHTLY_BOUND)."""
    mark_nightly(tmp_path, None if since is None else datetime.now(UTC) - since)

    result = refer(tmp_path, calls, referral, cut)

    assert result.returncode == 0, result.stderr


def test_it_refuses_to_start_while_another_diagnosis_runs(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """A running diagnosis holds a lock on the diagnoses directory until it ends."""
    private = data_root(tmp_path) / "diagnoses"
    private.mkdir(parents=True)
    another = os.open(private, os.O_RDONLY)
    try:
        fcntl.flock(another, fcntl.LOCK_EX)
        refused = refer(tmp_path, calls, referral, cut)
        assert recorded(calls) == []
    finally:
        os.close(another)
    after = refer(tmp_path, calls, referral, cut)

    assert refused.returncode == 1
    assert "another diagnosis is running" in refused.stderr
    assert after.returncode == 0, after.stderr
    assert len(diagnoses(tmp_path)) == 1


def test_what_a_diagnosis_that_was_killed_left_of_its_exports_goes_with_the_next_one(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """An exported history is real content, kept only while its diagnosis runs. Whoever holds
    the lock is the only diagnosis running, so any other's is left over."""
    left_over = data_root(tmp_path) / "diagnoses" / ".work-20261101T080000Z-killed"
    (left_over / "history" / "own").mkdir(parents=True)
    (left_over / "history" / "own" / "0001-0123456789ab.patch").write_text("made up\n")

    result = refer(tmp_path, calls, referral, cut)

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    assert list(private.parent.iterdir()) == [private]


def test_it_never_takes_the_data_roots_lock(tmp_path: Path, calls: Path, cut: Path, referral: Path):
    """A diagnosis reads a cut, which holds still, so a capture cycle holding the data root's
    lock doesn't stop it, and it never makes one wait."""
    lock = data_root(tmp_path) / "pa.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = refer(tmp_path, calls, referral, cut)

    assert result.returncode == 0, result.stderr


SALT = ("Okafor", "Ridgeline", "renews", "QXZ88")
"""Words of the made-up case, which nothing the script prints may hold."""

SLIPPED = """\
Traceback (most recent call last):
  File "/opt/pa/evals/src/pa_evals/findings.py", line 1, in parse_findings
pydantic_core.ValidationError: 1 validation error for Findings
summary
  String too long [input_value='Nell Okafor asked Ridgeline about a lease that renews, QXZ88']
"""


def test_what_it_prints_is_counts_and_names_of_checks_and_never_what_the_vms_command_let_slip(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """Whatever runs in the VM read real content. Of what its command prints, only the lines
    `pa-eval diagnose` is written to say are shown; the rest is kept for the owner."""
    stray = "The lease at Ridgeline renews in March.\n"

    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"stdout": stray + SESSION_SAYS + stray, "stderr": SLIPPED},
    )

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    name = private.name
    assert result.stdout == (
        f"diagnosis: {name}, over the cut {CUT_NAME}\n"
        "case: the cut alone\n"
        "session: $2.64, 77 turns\n"
        "resumes: 0\n"
        "revisions: 0\n"
        f"left: {private}\n"
        f"release: {name}: released\n"
        f"released: {tmp_path / 'released'}/{name}\n"
        "findings: 2 (1 fault, 1 noise)\n"
        "reproductions: 1, untested\n"
        "payloads: 3\n" + CHECKS
    )
    assert not [word for word in SALT if word in result.stdout + result.stderr]
    # The owner can still read what wasn't shown, and the developer is told how much there is.
    kept = private / "diagnose-output.txt"
    assert kept.read_text() == stray + stray + SLIPPED
    assert modes(private) == PRIVATE
    assert (
        f"7 lines pa-eval diagnose printed aren't shown: they are kept in {kept}" in result.stderr
    )


def test_what_a_command_that_started_no_session_let_slip_is_kept_for_the_owner_too(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = refer(
        tmp_path,
        calls,
        referral,
        cut,
        session={"exit": 1, "stdout": "", "stderr": SLIPPED, "leaves": False},
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert not [word for word in SALT if word in result.stderr]
    [private] = diagnoses(tmp_path)
    assert [path.name for path in private.iterdir()] == ["diagnose-output.txt"]
    assert (private / "diagnose-output.txt").read_text() == SLIPPED
    assert modes(private) == PRIVATE
    assert str(private / "diagnose-output.txt") in result.stderr


def test_what_the_release_check_let_slip_is_kept_for_the_owner_as_well(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    """The check reads the session's output and the case, so a traceback of its own can quote
    either. Its own errors never do."""
    said = SLIPPED + "pa-eval: the diagnosis read a case of other parts\n"

    result = refer(
        tmp_path, calls, referral, cut, release={"exit": 1, "stdout": "", "stderr": said}
    )

    assert result.returncode == 1
    assert "pa-eval: the diagnosis read a case of other parts\n" in result.stderr
    assert not [word for word in SALT if word in result.stdout + result.stderr]
    [private] = diagnoses(tmp_path)
    kept = private / "release-output.txt"
    assert kept.read_text() == SLIPPED
    assert modes(private) == PRIVATE
    assert f"5 lines pa-eval release printed aren't shown: they are kept in {kept}" in result.stderr


def live_store(root: Path) -> Path:
    """A directory laid out as a data root in use is, with its wiki and the wiki's git
    directory, as a dreamed copy has too."""
    for directory in ("inbox/assistant_chat", "l0/episodes", "wiki/people", "wiki.git"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.parametrize("which", ["the data root", "a store elsewhere"])
def test_the_live_store_is_never_a_wiki_of_the_case(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, which: str
):
    """A data root holds a wiki and its git directory, as a dreamed copy does. A case holds a
    cut of the store, and copies of the cut's wiki, never the store."""
    roots = {"the data root": data_root(tmp_path), "a store elsewhere": tmp_path / "restored"}
    live = live_store(roots[which])

    result = refer(tmp_path, calls, referral, cut, "--wiki", f"live={live}")

    assert result.returncode == 1
    assert f"the wiki under live ({live}) is a live store" in result.stderr
    assert recorded(calls) == []
    assert diagnoses(tmp_path) == []


def test_a_live_store_is_never_the_cut(tmp_path: Path, calls: Path, cut: Path, referral: Path):
    """A directory named as a cut is and holding a manifest, with what only a live store has."""
    live_store(cut)

    result = refer(tmp_path, calls, referral, cut)

    assert result.returncode == 1
    assert f"{cut} isn't a cut" in result.stderr
    assert recorded(calls) == []
    assert diagnoses(tmp_path) == []


NAMED_CUT = ("--name", "dreamed-drop", "--cut", "{cut}")
REFERRED = ("--referral", "{referral}", *NAMED_CUT)
"""The options every diagnosis needs, for `str.format` to fill in."""

INSIDE = DEPLOY.parents[1] / "evals" / "runs" / "private-diagnose-test-never-written"
"""A path inside the checkout (git-ignored), which no test ever makes."""


@pytest.mark.parametrize(
    "which", ["cut", "eval set", "run directory", "wiki", "earlier diagnosis", "data root"]
)
def test_a_part_of_the_case_or_the_private_directory_inside_the_repository_stops_it_unpushed(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, parts: dict[str, Path], which: str
):
    env = {}
    case = {
        "cut": cut,
        "eval set": parts["eval_set"],
        "run directory": parts["own"],
        "wiki": parts["copy"],
        "earlier diagnosis": parts["earlier"],
    }
    refused = INSIDE / "part"
    if which == "data root":
        # The diagnoses directory, where a diagnosis is left, is the data root's.
        env["PA_DATA_ROOT"] = str(INSIDE / "pa-home")
        refused = INSIDE / "pa-home" / "diagnoses"
    else:
        case[which] = refused

    result = diagnose(
        tmp_path,
        calls,
        *("--referral", str(referral), "--name", "dreamed-drop", "--cut", str(case["cut"])),
        *("--eval-set", str(case["eval set"])),
        *("--runs", f"own={case['run directory']}"),
        *("--wiki", f"dreamed={case['wiki']}"),
        *("--earlier", str(case["earlier diagnosis"])),
        env=env,
    )

    assert result.returncode == 1
    assert f"({refused}) is inside the repository" in result.stderr
    assert recorded(calls) == []
    assert not INSIDE.exists()


@pytest.mark.parametrize("where", ["inside the repository", "inside the data root"])
def test_a_released_directory_that_a_release_would_refuse_stops_it_before_a_session_is_spent(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, where: str
):
    released = {
        "inside the repository": INSIDE / "released",
        "inside the data root": data_root(tmp_path) / "released",
    }[where]

    result = refer(tmp_path, calls, referral, cut, env={"PA_RELEASED_DIR": str(released)})

    assert result.returncode == 1
    assert where in result.stderr
    assert recorded(calls) == []
    assert not released.exists()


@pytest.mark.parametrize(
    ("given_as", "said"),
    [
        (["--name", "dreamed-drop", "--cut", "{cut}"], "--referral FILE is needed"),
        (["--referral", "{referral}", "--cut", "{cut}"], "--name NAME is needed"),
        (["--referral", "{referral}", "--name", "dreamed-drop"], "--cut DIR is needed"),
        (["--referral", "{root}/no-referral.md", *NAMED_CUT], "referral not found"),
        (["--referral", "{referral}", "--name", "Dreamed drop!", "--cut", "{cut}"], "--name is"),
        (["--referral", "{referral}", "--name", "x" * 64, "--cut", "{cut}"], "--name is"),
        (["--referral", "{referral}", "--name", "dreamed-drop", "--cut", "{root}"], "isn't a cut"),
        ([*REFERRED, "--out", "{root}/diagnoses"], "drop --out"),
        ([*REFERRED, "--history", "own={root}/history"], "drop --history"),
        ([*REFERRED, "--revise", "{root}/odd"], "drop --revise"),
        ([*REFERRED, "--notes={root}/odd/notes.md"], "drop --notes"),
        ([*REFERRED, "--eval-set", "{root}/no-set.json"], "eval set not found"),
        ([*REFERRED, "--runs", "{root}/odd/no-runs"], "--runs takes LABEL=DIR"),
        ([*REFERRED, "--runs", "Own Runs={root}/odd/no-runs"], "a label is lowercase"),
        ([*REFERRED, "--runs", "own={root}/odd/no-runs"], "holds no eval runs"),
        ([*REFERRED, "--runs", "own={root}/odd/missing"], "run directory not found"),
        ([*REFERRED, "--wiki", "dreamed={root}/odd/no-runs"], "has no wiki/"),
        ([*REFERRED, "--wiki", "dreamed={root}/odd/tree-alone"], "has no wiki.git/"),
        ([*REFERRED, "--wiki", "own={cut}", "--wiki=own={cut}"], "the label own twice"),
        ([*REFERRED, "--earlier", "{root}/odd/no-runs"], "has no private analysis"),
        ([*REFERRED, "--earlier", "{root}/odd/not_a_name"], "isn't named as a diagnosis is"),
    ],
)
def test_options_that_dont_make_a_diagnosis_stop_it_before_the_vm_is_touched(
    tmp_path: Path, calls: Path, cut: Path, referral: Path, given_as: list[str], said: str
):
    root = data_root(tmp_path)
    (root / "odd" / "no-runs" / "run-x").mkdir(parents=True)
    (root / "odd" / "no-runs" / "run-x" / "results.json").write_text("{}\n")
    (root / "odd" / "tree-alone" / "wiki").mkdir(parents=True)
    (root / "odd" / "not_a_name").mkdir()
    places = {"root": root, "cut": cut, "referral": referral}

    result = diagnose(tmp_path, calls, *(word.format(**places) for word in given_as))

    assert result.returncode == 1
    assert said in result.stderr
    assert result.stdout == ""
    assert recorded(calls) == []
    assert diagnoses(tmp_path) == []


def test_the_longest_name_it_takes_makes_the_longest_name_a_diagnosis_may_have(
    tmp_path: Path, calls: Path, cut: Path, referral: Path
):
    result = diagnose(
        tmp_path, calls, "--referral", str(referral), "--name", "x" * 63, "--cut", str(cut)
    )

    assert result.returncode == 0, result.stderr
    [private] = diagnoses(tmp_path)
    assert NAME.fullmatch(private.name)
    assert not NAME.fullmatch(private.name + "x")


def lib_values(*names: str, env: dict[str, str] | None = None) -> list[str]:
    """The values lib.sh gives `names`, an array's words joined by spaces."""
    script = "; ".join(f'echo "${{{name}[*]}}"' for name in names)
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/owner", **(env or {})},
        check=True,
    )
    return result.stdout.splitlines()


def test_the_scripts_names_are_the_ones_the_commands_it_runs_go_by():
    refused, failed, diagnoses_directory, released, settings = lib_values(
        "PA_DIAGNOSIS_REFUSED",
        "PA_DIAGNOSIS_FAILED",
        "PA_DIAGNOSES",
        "PA_RELEASED",
        "PA_DIAGNOSTICIAN_SETTINGS",
    )

    # A refusal is told by the release command's own code, and each code means one thing.
    assert int(refused) == release_cli.REFUSED == REFUSED
    assert int(failed) == FAILED
    assert len({0, 1, REFUSED, FAILED}) == 4
    # It revises as often as a diagnosis may be revised, by the notes the check leaves.
    revisions, notes, vm_refused = lib_values(
        "PA_DIAGNOSIS_REVISIONS", "PA_DIAGNOSIS_NOTES", "PA_VM_EVAL_REFUSED"
    )
    assert int(revisions) == diagnosis.MAX_REVISIONS == 2
    assert notes == diagnosis.NOTES
    assert vm_refused == VM_REFUSED
    home = Path("/home/owner")
    assert Path(diagnoses_directory) == DataRoot(home / ".local" / "share" / "pa-home").diagnoses
    assert released == config.DEFAULT_RELEASED_DIR.replace("~", str(home))
    assert settings.split() == [
        config.DIAGNOSTICIAN_MODEL_VAR,
        config.DIAGNOSTICIAN_EFFORT_VAR,
        config.DIAGNOSTICIAN_MAX_TURNS_VAR,
        config.DIAGNOSTICIAN_TIMEOUT_VAR,
    ]


def test_a_released_directory_given_from_the_home_directory_is_read_as_the_release_reads_it():
    [released] = lib_values("PA_RELEASED", env={"PA_RELEASED_DIR": "~/released"})

    assert released == "/home/owner/released"


def test_the_scripts_usage_and_the_readme_name_every_part_of_a_case_and_every_exit_code():
    usage = DIAGNOSE.read_text().split("set -Eeuo pipefail")[0]
    readme = (DEPLOY / "README.md").read_text()

    for option in ("--referral", "--name", "--cut", "--eval-set", "--runs", "--wiki", "--earlier"):
        assert option in usage.split("\n#\n")[0]
    for code in (0, 1, REFUSED, FAILED):
        assert re.search(rf"\b{code}, ", usage.split("Exit codes:")[1])
    assert "| `diagnose.sh --referral FILE --name NAME --cut DIR [case parts]` |" in readme
