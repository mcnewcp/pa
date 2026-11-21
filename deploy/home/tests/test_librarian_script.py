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


def test_the_scripts_usage_and_the_readme_name_every_librarian_command():
    usage = (DEPLOY / "librarian.sh").read_text().split("set -euo pipefail")[0]
    readme = (DEPLOY / "README.md").read_text()

    for command in ("run", "dream", "replay", "seed"):
        assert f"librarian.sh {command}" in usage
    assert "`librarian.sh run\\|dream\\|replay\\|seed …`" in readme
