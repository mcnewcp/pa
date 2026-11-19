"""The code's way into the fenced assistant VM (#91): the devbox bundles the commit to deploy from
its own checkout (`make_bundle`), and install-code.sh, run as root in the VM, checks that commit
out of the bundle and syncs its venv from the wheelhouse the devbox built, offline. Here it runs
on a scratch repository and a stand-in `uv` that records how it was run."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
INSTALL = DEPLOY / "install-code.sh"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

STAND_IN_UV = """\
import json, os, sys
env = {k: v for k, v in os.environ.items() if k.startswith("UV_")}
with open(os.environ["UV_CALLS"], "a") as calls:
    calls.write(json.dumps({"argv": sys.argv[1:], "env": env, "cwd": os.getcwd()}) + "\\n")
"""


def git(repo: Path, *args: str) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo.parent), **GIT_ENV}
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    ).stdout.strip()


def source_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A checkout with two commits; returns it and the two commits, oldest first."""
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "--quiet")
    commits = []
    for version in ("one", "two"):
        (repo / "VERSION").write_text(f"{version}\n")
        git(repo, "add", "VERSION")
        git(repo, "commit", "--quiet", "-m", version)
        commits.append(git(repo, "rev-parse", "HEAD"))
    return repo, commits[0], commits[1]


def bundle(tmp_path: Path, repo: Path, commit: str) -> Path:
    out = tmp_path / "vm" / "var" / "cache" / "pa" / "pa.bundle"
    out.parent.mkdir(parents=True, exist_ok=True)
    args = (str(repo), commit, str(out))
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; make_bundle "$@"', "-", *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **GIT_ENV},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return out


def install(
    tmp_path: Path, bundle: Path, commit: str, target: Path
) -> subprocess.CompletedProcess[str]:
    uv = tmp_path / "bin" / "uv"
    uv.parent.mkdir(exist_ok=True)
    uv.write_text(f"#!{sys.executable}\n{STAND_IN_UV}")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    wheelhouse = tmp_path / "vm" / "var" / "cache" / "pa" / "wheelhouse"
    (wheelhouse / "wheels-v6").mkdir(parents=True, exist_ok=True)
    args = [str(target), str(bundle), commit, str(uv), str(wheelhouse), "/usr/bin/python3"]
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "UV_CALLS": str(tmp_path / "uv-calls"),
            # The VM's /etc/gitconfig, where safe.directory goes.
            "GIT_CONFIG_SYSTEM": str(tmp_path / "gitconfig"),
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        check=False,
    )


def uv_calls(tmp_path: Path) -> list[dict]:
    path = tmp_path / "uv-calls"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_commit_is_checked_out_from_the_bundle_and_synced_offline_from_the_wheelhouse(
    tmp_path: Path,
):
    repo, _, latest = source_repo(tmp_path)
    target = tmp_path / "vm" / "opt" / "pa"

    result = install(tmp_path, bundle(tmp_path, repo, latest), latest, target)

    assert result.returncode == 0, result.stderr
    assert git(target, "rev-parse", "HEAD") == latest
    assert (target / "VERSION").read_text() == "two\n"
    [sync] = uv_calls(tmp_path)
    assert sync["cwd"] == str(target)
    assert sync["argv"] == [
        "sync", "--locked", "--no-dev", "--compile-bytecode", "--python", "/usr/bin/python3"
    ]  # fmt: skip
    assert sync["env"]["UV_OFFLINE"] == "1"
    assert sync["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert sync["env"]["UV_CACHE_DIR"].endswith("/var/cache/pa/wheelhouse")


def test_the_checkout_is_roots_alone_and_what_was_pushed_is_cleaned_up(tmp_path: Path):
    repo, _, latest = source_repo(tmp_path)
    target = tmp_path / "vm" / "opt" / "pa"
    pushed = bundle(tmp_path, repo, latest)

    install(tmp_path, pushed, latest, target)

    assert not pushed.exists()
    assert not (pushed.parent / "wheelhouse").exists()
    loose = [p for p in target.rglob("*") if not p.is_symlink() and p.stat().st_mode & 0o022]
    assert loose == []
    assert f"directory = {target}" in (tmp_path / "gitconfig").read_text()


def test_an_older_commit_replaces_the_checkout_and_no_remote_names_the_internet(tmp_path: Path):
    """The live VM's checkout was cloned from GitHub, which the fenced VM can't reach."""
    repo, first, latest = source_repo(tmp_path)
    target = tmp_path / "vm" / "opt" / "pa"
    install(tmp_path, bundle(tmp_path, repo, latest), latest, target)
    git(target, "remote", "add", "origin", "https://github.com/mcnewcp/pa.git")

    result = install(tmp_path, bundle(tmp_path, repo, first), first, target)

    assert result.returncode == 0, result.stderr
    assert git(target, "rev-parse", "HEAD") == first
    assert git(target, "remote") == ""


def test_a_bundle_that_doesnt_hold_the_commit_installs_nothing(tmp_path: Path):
    repo, first, latest = source_repo(tmp_path)
    target = tmp_path / "vm" / "opt" / "pa"

    result = install(tmp_path, bundle(tmp_path, repo, first), latest, target)

    assert result.returncode != 0
    assert f"the bundle holds {first}, not {latest}" in result.stderr
    assert uv_calls(tmp_path) == []
