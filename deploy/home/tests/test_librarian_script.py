"""librarian.sh: `pa librarian` on the devbox, its sessions in the librarian's VM. The script
runs here whole, on a stand-in `uv` that records the command it was asked to run and the
environment it was given."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parents[1]

STAND_IN_UV = """\
import json, os, sys
env = {k: v for k, v in os.environ.items() if k.startswith("PA_")}
with open(os.environ["UV_LOG"], "w") as log:
    json.dump({"argv": sys.argv[1:], "env": env}, log)
"""


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home whose data root has its owner, as the wizard leaves it."""
    home = tmp_path / "home"
    data_root = home / ".local" / "share" / "pa-home"
    data_root.mkdir(parents=True)
    (data_root / "owner.env").write_text(
        "PA_OWNER_NAME='Argus McNevans'\nPA_TIME_ZONE=America/Chicago\n"
    )
    return home


def run_script(home: Path, tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> dict:
    """Runs librarian.sh with `args`, and returns what its `uv` was called with."""
    stand_ins = tmp_path / "bin"
    stand_ins.mkdir()
    uv = stand_ins / "uv"
    uv.write_text(f"#!{sys.executable}\n{STAND_IN_UV}")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "uv.json"
    result = subprocess.run(
        ["bash", str(DEPLOY / "librarian.sh"), *args],
        capture_output=True,
        text=True,
        env={
            "HOME": str(home),
            "USER": "owner",
            "PATH": f"{stand_ins}{os.pathsep}/usr/bin:/bin",
            "UV_LOG": str(log),
            **(env or {}),
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(log.read_text())


@pytest.mark.parametrize(
    "args",
    [
        ("dream",),
        ("run",),
        ("replay", "/srv/replays/argus", "--corpus", "evals/corpus/payloads", "--resume"),
    ],
    ids=lambda args: args[0],
)
def test_a_librarian_command_is_passed_through_with_its_sessions_in_the_vm(
    home: Path, tmp_path: Path, args: tuple[str, ...]
):
    """`dream` like `run`: the same data root, owner, and sandbox, and never a librarian
    command from the environment, which could run claude outside the VM."""
    call = run_script(home, tmp_path, *args, env={"PA_LIBRARIAN_COMMAND": "claude"})

    assert call["argv"] == ["run", "--project", str(REPO), "pa", "librarian", *args]
    env = call["env"]
    assert env["PA_DATA_DIR"] == str(home / ".local" / "share" / "pa-home")
    assert env["PA_LIBRARIAN_SANDBOX"] == str(DEPLOY / "librarian-sandbox.sh")
    assert (env["PA_OWNER_NAME"], env["PA_TIME_ZONE"]) == ("Argus McNevans", "America/Chicago")
    assert "PA_LIBRARIAN_COMMAND" not in env


REAL_OWNER = "PA_OWNER_NAME='Riley Real'\nPA_OWNER_EMAILS=riley@example.org\n"
"""The data root's owner.env, for an instance whose owner isn't the corpus's (and is as
made up as the corpus's is)."""
FICTIONAL_OWNER = (
    "PA_OWNER_NAME='Argus McNevans'\nPA_OWNER_EMAILS=argus@example.com\n"
    "PA_TIME_ZONE=America/Chicago\n"
)
CORPUS = ("--corpus", "evals/corpus/payloads")


@pytest.mark.parametrize(
    "args",
    [
        ("dream", "/srv/replays/argus-dreamed", *CORPUS),
        ("replay", "/srv/replays/argus", "--corpus=evals/corpus/payloads", "--as-of", "2026-10-14"),
    ],
    ids=lambda args: args[0],
)
def test_a_corpus_is_run_as_the_owner_named_for_it_in_place_of_the_data_roots(
    home: Path, tmp_path: Path, args: tuple[str, ...]
):
    (home / ".local" / "share" / "pa-home" / "owner.env").write_text(
        REAL_OWNER + "PA_OWNER_OTHER_NAMES=Ri\nPA_TIME_ZONE=Europe/Lisbon\n"
    )
    fictional = tmp_path / "argus.env"
    fictional.write_text(FICTIONAL_OWNER)

    call = run_script(home, tmp_path, *args, env={"PA_LIBRARIAN_OWNER_ENV": str(fictional)})

    assert call["argv"] == ["run", "--project", str(REPO), "pa", "librarian", *args]
    env = call["env"]
    assert (env["PA_OWNER_NAME"], env["PA_OWNER_EMAILS"]) == (
        "Argus McNevans",
        "argus@example.com",
    )
    assert env["PA_TIME_ZONE"] == "America/Chicago"
    # Nothing of the data root's owner reaches the run: not even what the file leaves out.
    assert "PA_OWNER_OTHER_NAMES" not in env
    assert env["PA_LIBRARIAN_SANDBOX"] == str(DEPLOY / "librarian-sandbox.sh")


@pytest.mark.parametrize("eval_set", ["eval_set.json", "eval_set-scale.json"])
def test_the_frozen_corporas_owner_file_names_the_owner_their_eval_sets_ask_as(
    home: Path, tmp_path: Path, eval_set: str
):
    corpus = REPO / "evals" / "corpus"
    asked = json.loads((corpus / eval_set).read_text())

    call = run_script(
        home,
        tmp_path,
        "dream",
        "/srv/replays/dreamed",
        *CORPUS,
        env={"PA_LIBRARIAN_OWNER_ENV": str(corpus / "owner.env")},
    )

    env = call["env"]
    assert env["PA_OWNER_NAME"] == asked["owner"]["name"]
    assert env["PA_OWNER_EMAILS"].split(",") == asked["owner"]["email_addresses"]
    assert env["PA_OWNER_OTHER_NAMES"].split(",") == asked["owner"]["other_names"]
    # The zone the sets' as-of time is written in: 20:00 on a day Chicago is at -05:00.
    assert env["PA_TIME_ZONE"] == "America/Chicago"
    assert asked["as_of"].endswith("-05:00")


def librarian_sh(home: Path, tmp_path: Path, *args: str, env: dict[str, str]):
    """Runs librarian.sh with no `uv` to reach: for a command it must refuse."""
    return subprocess.run(
        ["bash", str(DEPLOY / "librarian.sh"), *args],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "USER": "owner", "PATH": "/usr/bin:/bin", **env},
        check=False,
    )


@pytest.mark.parametrize(
    "args",
    [
        ("run",),
        ("dream",),
        ("dream", "/srv/replays/cut-dreamed", "--cut", "/srv/cuts/20261118T202737Z"),
        ("replay", "/srv/replays/rebuilt"),
        ("seed", "evals/wikis/scale/665463df6ee7"),
    ],
    ids=lambda args: " ".join(args[:2]),
)
def test_the_data_roots_own_l0_and_wiki_are_never_run_as_another_owner(
    home: Path, tmp_path: Path, args: tuple[str, ...]
):
    fictional = tmp_path / "argus.env"
    fictional.write_text(FICTIONAL_OWNER)

    result = librarian_sh(home, tmp_path, *args, env={"PA_LIBRARIAN_OWNER_ENV": str(fictional)})

    assert result.returncode != 0
    assert "PA_LIBRARIAN_OWNER_ENV goes with --corpus" in result.stderr


def test_an_owner_file_that_isnt_there_stops_the_run(home: Path, tmp_path: Path):
    missing = tmp_path / "nobody.env"

    result = librarian_sh(
        home, tmp_path, "dream", "/srv/t", *CORPUS, env={"PA_LIBRARIAN_OWNER_ENV": str(missing)}
    )

    assert result.returncode != 0
    assert f"{missing} is missing" in result.stderr


# Dreaming a released reproduction's starting wiki (#168): the eval harness's command, which
# knows a reproduction's layout, with its sessions in the librarian's VM all the same.

REPRODUCTION = "/srv/released/20261120T101500Z-made-up/reproductions/f1"


def replays(home: Path) -> Path:
    return home / ".local" / "share" / "pa-home" / "replays"


@pytest.mark.parametrize(
    "named", [("--reproduction", REPRODUCTION), (f"--reproduction={REPRODUCTION}",)], ids=len
)
def test_dreaming_a_reproduction_is_handed_to_the_eval_harness_with_its_sessions_in_the_vm(
    home: Path, tmp_path: Path, named: tuple[str, ...]
):
    (home / ".local" / "share" / "pa-home" / "owner.env").write_text(
        REAL_OWNER + "PA_OWNER_OTHER_NAMES=Ri\nPA_TIME_ZONE=Europe/Lisbon\n"
    )
    copy = replays(home) / "f1-dreamed"

    call = run_script(
        home, tmp_path, "dream", str(copy), *named, env={"PA_LIBRARIAN_COMMAND": "claude"}
    )

    assert call["argv"] == [
        *("run", "--project", str(REPO)),
        *("pa-eval", "reproduction", "dream", REPRODUCTION, str(copy)),
    ]
    env = call["env"]
    assert env["PA_LIBRARIAN_SANDBOX"] == str(DEPLOY / "librarian-sandbox.sh")
    assert "PA_LIBRARIAN_COMMAND" not in env
    # The time zone is the instance's, which the command reads from the environment. The owner
    # is the reproduction's eval set's, so nothing else of the data root's owner reaches it.
    assert env["PA_TIME_ZONE"] == "Europe/Lisbon"
    assert not {"PA_OWNER_NAME", "PA_OWNER_EMAILS", "PA_OWNER_OTHER_NAMES"} & env.keys()


def test_a_reproductions_owner_is_never_named_by_a_file(home: Path, tmp_path: Path):
    fictional = tmp_path / "argus.env"
    fictional.write_text(FICTIONAL_OWNER)
    args = ("dream", str(replays(home) / "f1-dreamed"), "--reproduction", REPRODUCTION)

    result = librarian_sh(home, tmp_path, *args, env={"PA_LIBRARIAN_OWNER_ENV": str(fictional)})

    assert result.returncode != 0
    assert "PA_LIBRARIAN_OWNER_ENV doesn't go with --reproduction" in result.stderr


@pytest.mark.parametrize(
    "copy", ["{home}/f1-dreamed", "{replays}", "{replays}/../wiki/f1-dreamed"], ids=str
)
def test_a_copy_of_a_reproductions_wiki_outside_the_replays_is_refused(
    home: Path, tmp_path: Path, copy: str
):
    copy = copy.format(home=home, replays=replays(home))

    result = librarian_sh(home, tmp_path, "dream", copy, "--reproduction", REPRODUCTION, env={})

    assert result.returncode != 0
    assert f"must be under {replays(home)}" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("dream", "--reproduction", REPRODUCTION),
        ("dream", "COPY", "--reproduction"),
        ("dream", "COPY", "--reproduction", REPRODUCTION, "--corpus", "evals/corpus/payloads"),
        ("dream", "COPY", f"--reproduction={REPRODUCTION}", "--cut", "/srv/cuts/20261118T202737Z"),
        ("replay", "COPY", "--reproduction", REPRODUCTION),
    ],
    ids=" ".join,
)
def test_a_reproduction_is_dreamed_by_one_form_of_the_command(
    home: Path, tmp_path: Path, args: tuple[str, ...]
):
    copy = str(replays(home) / "f1-dreamed")
    args = tuple(copy if arg == "COPY" else arg for arg in args)

    result = librarian_sh(home, tmp_path, *args, env={})

    assert result.returncode != 0
    assert "usage: librarian.sh dream <copy> --reproduction <dir>" in result.stderr


def test_the_scripts_usage_and_the_readme_name_every_librarian_command():
    usage = (DEPLOY / "librarian.sh").read_text().split("set -euo pipefail")[0]
    readme = (DEPLOY / "README.md").read_text()

    assert "librarian.sh dream <copy> --reproduction <dir>" in usage
    for command in ("run", "dream", "replay", "seed"):
        assert f"librarian.sh {command}" in usage
    assert "`librarian.sh run\\|dream\\|replay\\|seed …`" in readme
