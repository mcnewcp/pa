"""The capture helper and its sudo rule are plain files, pushed into the assistant's VM as they
are, so the user names and paths in them are written out rather than read from lib.sh. These
check they're lib.sh's, so the two can't drift apart."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
VERBS = ("prompt", "answer", "stop")


def lib(*names: str) -> dict[str, str]:
    """The values lib.sh gives `names`, for the assistant's VM."""
    script = f'source "{DEPLOY / "lib.sh"}"; ' + "; ".join(f'echo "${name}"' for name in names)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "USER": "owner"},
        check=True,
    )
    return dict(zip(names, result.stdout.splitlines(), strict=True))


def test_the_sudo_rule_lets_lib_shs_assistant_run_lib_shs_helper_as_lib_shs_capture_user():
    names = lib("PA_ASSISTANT_USER", "PA_CAPTURE_USER", "PA_VM_CAPTURE_HELPER")
    rules = [
        line
        for line in (DEPLOY / "sudoers-pa-capture").read_text().splitlines()
        if line and not line.startswith("#")
    ]

    helper = names["PA_VM_CAPTURE_HELPER"]
    assert rules == [
        f"{names['PA_ASSISTANT_USER']} ALL=({names['PA_CAPTURE_USER']}) NOPASSWD: "
        + ", ".join(f"{helper} {verb}" for verb in VERBS)
    ]


def test_the_helper_runs_lib_shs_hook_over_lib_shs_inbox_and_state():
    names = lib(
        "PA_VM_HOOK_PYTHON",
        "PA_VM_PROJECT",
        "PA_VM_CHAT_INBOX",
        "PA_VM_CAPTURE_STATE",
        "PA_VM_CAPTURE_HELPER",
    )
    text = (DEPLOY / "pa-capture").read_text()
    command = text[text.index("exec ") :].replace("\\\n", " ")

    assert shlex.split(command.splitlines()[0]) == [
        "exec",
        names["PA_VM_HOOK_PYTHON"],
        "-I",
        f"{names['PA_VM_PROJECT']}/.claude/hooks/capture_exchange.py",
        "$1",
        "--inbox",
        names["PA_VM_CHAT_INBOX"],
        "--state",
        names["PA_VM_CAPTURE_STATE"],
    ]
    assert f"at {names['PA_VM_CAPTURE_HELPER']} in the assistant's VM" in text
