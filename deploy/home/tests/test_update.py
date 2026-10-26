"""update.sh for the assistant's VM: the code, owner.env and the agent project go in as root's,
read-only to the assistant, behind the managed settings (#92). It runs here on a stand-in
`incus` that records each call and what was piped to it."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
UPDATE = DEPLOY / "update.sh"

STAND_IN_INCUS = """\
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" else b""
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps({"argv": argv, "stdin": stdin.decode()}) + "\\n")
# Remote Control isn't enabled yet, so the update doesn't start it.
if "is-enabled" in argv:
    sys.exit(1)
"""


def update(tmp_path: Path, *args: str) -> list[dict[str, Any]]:
    """Runs update.sh on the stand-in `incus`; returns its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    incus = bin_dir / "incus"
    incus.write_text(f"#!{sys.executable}\n{STAND_IN_INCUS}")
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n')
    for script in (incus, sudo):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    data_root = tmp_path / "home" / ".local" / "share" / "pa-home-synthetic"
    data_root.mkdir(parents=True)
    (data_root / "owner.env").write_text("PA_OWNER_NAME='Argus McNevans'\n")
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(UPDATE), *args],
        input="",
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(calls),
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in calls.read_text().splitlines()]


def as_root(call: dict[str, Any]) -> bool:
    """Whether the call runs a script in the VM as root, not through runuser."""
    return call["argv"][:5] == ["exec", "pa-home", "--", "bash", "-s"]


def index_of(calls: list[dict[str, Any]], found: Any) -> int:
    [index] = [i for i, call in enumerate(calls) if found(call)]
    return index


def test_the_code_and_its_venv_go_in_as_roots(tmp_path: Path):
    calls = update(tmp_path, "v0.13")

    [deploy] = [c for c in calls if "sync --locked" in c["stdin"]]
    assert as_root(deploy)
    assert deploy["argv"][6:9] == ["https://github.com/mcnewcp/pa.git", "/opt/pa", "v0.13"]


def test_owner_env_goes_in_as_roots_readable_by_the_assistant(tmp_path: Path):
    calls = update(tmp_path, "main")

    [push] = [
        c["argv"]
        for c in calls
        if c["argv"][:2] == ["file", "push"] and c["argv"][-1] == "pa-home/etc/pa/owner.env"
    ]
    assert push[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]
    assert push[-2].endswith("/pa-home-synthetic/owner.env")


def test_root_renders_the_agent_project_behind_the_managed_settings(tmp_path: Path):
    calls = update(tmp_path, "main")

    fences = index_of(
        calls, lambda c: c["argv"][-1:] == ["pa-home/etc/claude-code/managed-settings.json"]
    )
    render = index_of(
        calls, lambda c: c["stdin"] == (DEPLOY / "render-agent-project.sh").read_text()
    )
    assert fences < render
    assert as_root(calls[render])
    assert calls[render]["argv"][6:] == [
        "/opt/pa",
        "/etc/pa/owner.env",
        "/srv/pa/agent",
        "/srv/pa/l0",
        "/srv/pa/scratch",
        "/srv/pa/wiki",
        "/srv/pa/status/status.json",
    ]


def test_remote_control_runs_in_the_rendered_project(tmp_path: Path):
    calls = update(tmp_path, "main")

    [unit] = [c["stdin"] for c in calls if "[Service]" in c["stdin"]]
    assert "WorkingDirectory=/srv/pa/agent\n" in unit
