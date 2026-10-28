"""update.sh for the assistant's VM, which reaches nothing but the Anthropic API (#91): the devbox
pushes everything in (the code at a ref as a git bundle, a wheelhouse for its lock, uv, and a
pinned, signature-checked Claude Code) and nothing in the VM goes online. The code, owner.env and
the agent project go in as root's, read-only to the assistant, behind the managed settings (#92).
It runs here on stand-ins (conftest.py) and on this checkout's own commits."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
UPDATE = DEPLOY / "update.sh"
REPO = DEPLOY.parents[1]
FINGERPRINT = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
CLAUDE_VERSION = "2.1.308"
API = "160.79.104.10"


def head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def downloaded_claude(home: Path, binary: bytes = b"claude 2.1.308") -> None:
    """The pinned release, already in the devbox's cache."""
    cache = home / ".cache" / "pa" / "claude-code" / CLAUDE_VERSION
    cache.mkdir(parents=True)
    (cache / "claude-linux-x64").write_bytes(binary)
    checksum = hashlib.sha256(b"claude 2.1.308").hexdigest()
    manifest = {"version": CLAUDE_VERSION, "platforms": {"linux-x64": {"checksum": checksum}}}
    (cache / "manifest.json").write_text(json.dumps(manifest))
    (cache / "manifest.json.sig").write_text("sig")
    (cache / "claude-code.asc").write_text("key")


def update(stand_ins: Any, *args: str, claude: bytes = b"claude 2.1.308"):
    for prog in ("uv", "gpg", "getent", "curl"):
        stand_ins.add(prog)
    stand_ins.answer("gpg", "--fingerprint", stdout=f"fpr:::::::::{FINGERPRINT}:\n")
    stand_ins.answer("gpg", "--verify", stdout=f"[GNUPG:] VALIDSIG {FINGERPRINT} 2026\n")
    stand_ins.answer("curl", exit=22)
    for host in ("api.anthropic.com", "platform.claude.com"):
        stand_ins.answer("getent", host, stdout=f"{API} STREAM {host}\n")
    stand_ins.answer("incus", "exec", "/usr/bin/python3", "-c", stdout="3.14\n")
    stand_ins.answer("incus", "exec", "cat /etc/hosts", stdout="127.0.0.1 localhost\n")
    stand_ins.answer("incus", "--version", stdout=f"{CLAUDE_VERSION} (Claude Code)\n")
    # Remote Control isn't enabled yet, so the update doesn't start it.
    stand_ins.answer("incus", "is-enabled", exit=1)
    data_root = stand_ins.home / ".local" / "share" / "pa-home"
    data_root.mkdir(parents=True)
    (data_root / "owner.env").write_text("PA_OWNER_NAME='Argus McNevans'\n")
    downloaded_claude(stand_ins.home, claude)
    return stand_ins.run(UPDATE, *args)


def updated(stand_ins: Any, *args: str) -> list[Any]:
    result = update(stand_ins, *args)
    assert result.returncode == 0, result.stderr
    return stand_ins.calls


def is_root_script(call: Any, script: str) -> bool:
    """Whether the call pipes `script` from deploy/home to bash as root in the VM."""
    return (
        call.prog == "incus"
        and call.argv[:5] == ["exec", "pa-home", "--", "bash", "-s"]
        and call.stdin == (DEPLOY / script).read_text()
    )


def push_to(calls: list[Any], path: str) -> Any:
    [push] = [c for c in calls if c.argv[:2] == ["file", "push"] and c.argv[-1] == f"pa-home{path}"]
    return push


def test_the_ref_goes_in_as_a_bundle_and_is_checked_out_by_root(stand_ins):
    calls = updated(stand_ins, "HEAD")

    bundle = push_to(calls, "/var/cache/pa/pa.bundle")
    assert bundle.argv[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0600"]
    install = stand_ins.index(lambda c: is_root_script(c, "install-code.sh"))
    assert calls.index(bundle) < install
    assert calls[install].argv[6:] == [
        "/opt/pa",
        "/var/cache/pa/pa.bundle",
        head(),
        "/usr/local/bin/uv",
        "/var/cache/pa/wheelhouse",
        "/usr/bin/python3",
    ]


def test_the_wheelhouse_is_built_on_the_devbox_for_the_vms_python_and_pushed(stand_ins):
    calls = updated(stand_ins, "HEAD")

    [build] = stand_ins.of("uv")
    assert build.argv[:4] == ["sync", "--quiet", "--locked", "--no-dev"]
    assert build.argv[-2:] == ["--python", "3.14"]
    assert "UV_CACHE_DIR" in build.env
    [unpack] = [c for c in calls if c.prog == "incus" and "/var/cache/pa/wheelhouse" in c.line
                and "tar" in c.line]  # fmt: skip
    assert "--no-same-owner" in unpack.line
    uv = push_to(calls, "/usr/local/bin/uv")
    assert uv.argv[-2] == str(stand_ins.bin / "uv")
    assert uv.argv[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0755"]


def test_the_pinned_claude_code_goes_in_as_roots_and_runs_remote_control(stand_ins):
    calls = updated(stand_ins, "HEAD")

    claude = push_to(calls, "/usr/local/bin/claude")
    assert claude.pushed == "claude 2.1.308"
    assert claude.argv[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0755"]
    # The copy the assistant installed itself (curl | bash), which it could replace, is gone.
    assert any(
        "rm -rf /home/assistant/.local/bin/claude /home/assistant/.local/share/claude" in c.line
        for c in calls
    )
    [unit] = [c.stdin for c in calls if "[Service]" in c.stdin]
    assert "ExecStart=/usr/local/bin/claude remote-control\n" in unit


def test_nothing_the_update_runs_in_the_vm_goes_online(stand_ins):
    calls = updated(stand_ins, "HEAD")

    in_vm = [c for c in calls if c.prog == "incus" and c.argv[:1] == ["exec"]]
    assert in_vm
    for call in in_vm:
        text = call.line + "\n" + call.stdin
        for online in ("curl", "wget", "git clone", "apt-get", "https://", "astral.sh"):
            assert online not in text, (online, call.line)


def test_the_api_hosts_are_pinned_before_remote_control_starts(stand_ins):
    calls = updated(stand_ins, "HEAD")

    pins = stand_ins.index(lambda c: c.argv[-1:] == ["pa-home/etc/hosts"])
    unit = stand_ins.index(lambda c: "[Service]" in c.stdin)
    assert pins < unit
    assert f"{API} platform.claude.com" in calls[pins].pushed


def test_owner_env_goes_in_as_roots_readable_by_the_assistant(stand_ins):
    push = push_to(updated(stand_ins, "HEAD"), "/etc/pa/owner.env")

    assert push.argv[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]
    assert push.argv[-2].endswith("/pa-home/owner.env")


def test_root_renders_the_agent_project_behind_the_managed_settings(stand_ins):
    calls = updated(stand_ins, "HEAD")

    fences = stand_ins.index(
        lambda c: c.argv[-1:] == ["pa-home/etc/claude-code/managed-settings.json"]
    )
    render = stand_ins.index(lambda c: is_root_script(c, "render-agent-project.sh"))
    assert fences < render
    assert calls[render].argv[6:] == [
        "/opt/pa",
        "/etc/pa/owner.env",
        "/srv/pa/agent",
        "/srv/pa/l0",
        "/srv/pa/scratch",
        "/srv/pa/wiki",
        "/srv/pa/status/status.json",
    ]
    [unit] = [c.stdin for c in calls if "[Service]" in c.stdin]
    assert "WorkingDirectory=/srv/pa/agent\n" in unit


def test_a_ref_this_checkout_doesnt_have_stops_before_the_vm_is_touched(stand_ins):
    result = update(stand_ins, "no-such-ref-91")

    assert result.returncode != 0
    assert "no-such-ref-91 isn't a commit in this checkout" in result.stderr
    assert stand_ins.of("incus") == []


def test_a_claude_code_that_fails_its_check_stops_before_the_vm_is_touched(stand_ins):
    result = update(stand_ins, "HEAD", claude=b"tampered")

    assert result.returncode != 0
    assert "doesn't match the signed manifest" in result.stderr
    assert not [c for c in stand_ins.of("incus") if "snapshot" in c.argv]
