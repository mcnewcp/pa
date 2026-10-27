"""The home VM's capture hooks, as Claude Code fires them there, standing in a test's directories.

In the VM, the managed settings (deploy/home/managed-settings.json) hold the only hooks that run
(#92). Each capture hook is a shell command that does nothing unless the session's environment
turns capture on, and otherwise runs the capture helper as the capture user through sudo (#94).
The helper, root's, runs the rendered agent project's hook script with the code's interpreter,
the inbox directory and its state directory written into it.

`VmHooks` fires them the same way: each command through the shell, with the hook input as JSON
on stdin and the session's environment plus CLAUDE_PROJECT_DIR. The VM's paths stand for a
project rendered here, this interpreter, and temporary directories; `sudo` for a stand-in that
lets through only what the repo's sudoers rule allows, and runs it with the environment reset,
as sudo does.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pa_core.owner import Owner
from pa_home.agent_project import render_agent_project

DEPLOY = Path(__file__).parents[3] / "deploy" / "home"
MANAGED_SETTINGS = DEPLOY / "managed-settings.json"
HELPER = DEPLOY / "pa-capture"
SUDOERS = DEPLOY / "sudoers-pa-capture"

VM_PROJECT = "/srv/pa/agent"
VM_PYTHON = "/opt/pa/.venv/bin/python"
VM_INBOX = "/srv/pa/inbox/assistant_chat"
VM_STATE = "/var/lib/pa-capture"
VM_HELPER = "/usr/local/libexec/pa-capture"
VM_SUDO = "/usr/bin/sudo"

HOOK_OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))

STAND_IN_SUDO = """\
import os, sys
[rule] = [line for line in open(os.environ["PA_TEST_SUDOERS"])
          if line.strip() and not line.startswith("#")]
argv = sys.argv[1:]
# The rule is the assistant's, to run commands as capture.
if not rule.startswith("assistant ALL=(capture) NOPASSWD:"):
    sys.exit("sudo: not allowed")
# sudo -n -u capture COMMAND VERB: only what the rule names, word for word.
if argv[:3] != ["-n", "-u", "capture"] or len(argv) != 5:
    sys.exit("sudo: not allowed")
command = " ".join(argv[3:])
allowed = rule.split("NOPASSWD:", 1)[1].split(",")
if command not in [entry.strip() for entry in allowed]:
    sys.exit("sudo: not allowed")
os.execve(argv[3], argv[3:], {"PATH": "/usr/bin:/bin"})
"""


class VmHooks:
    """The managed settings' hooks, with the VM's paths standing for this test's."""

    def __init__(self, root: Path) -> None:
        self.project = root / "rendered"
        render_agent_project(
            self.project, owner=HOOK_OWNER, l0=root / "l0", scratch=root / "scratch"
        )
        self.inbox = root / "data" / "inbox" / "assistant_chat"
        self.inbox.mkdir(parents=True)
        self.state = root / "pa-capture"
        self.state.mkdir(mode=0o700)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.helper = bin_dir / "pa-capture"
        self.sudo = bin_dir / "sudo"
        self.sudoers = root / "sudoers"
        self.helper.write_text(self._in_test(HELPER.read_text()))
        self.sudo.write_text(f"#!{sys.executable}\n{STAND_IN_SUDO}")
        self.sudoers.write_text(self._in_test(SUDOERS.read_text()))
        for script in (self.helper, self.sudo):
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
        self.settings: dict[str, Any] = json.loads(self._in_test(MANAGED_SETTINGS.read_text()))

    def _in_test(self, text: str) -> str:
        for vm, here in (
            (VM_HELPER, str(self.helper)),
            (VM_SUDO, str(self.sudo)),
            (VM_PROJECT, str(self.project)),
            (VM_PYTHON, sys.executable),
            (VM_INBOX, str(self.inbox)),
            (VM_STATE, str(self.state)),
        ):
            text = text.replace(vm, here)
        return text

    def environment(self, env: dict[str, str]) -> dict[str, str]:
        """A session's environment: this one's without capture, then `env`."""
        base = {k: v for k, v in os.environ.items() if not k.startswith("PA_CAPTURE")}
        return {
            **base,
            "CLAUDE_PROJECT_DIR": str(self.project),
            "PA_TEST_SUDOERS": str(self.sudoers),
            **env,
        }

    def fire(
        self,
        hook_input: dict[str, Any],
        env: dict[str, str] | None = None,
        *,
        stdin: str | None = None,
    ) -> list[subprocess.CompletedProcess[str]]:
        """Runs each hook the managed settings give `hook_input`'s event (and tool, if any),
        with `hook_input` as JSON on stdin, or `stdin` in its place."""
        event = hook_input["hook_event_name"]
        results = []
        for group in self.settings["hooks"].get(event, []):
            matcher = group.get("matcher")
            if matcher and matcher != hook_input.get("tool_name"):
                continue
            for hook in group["hooks"]:
                assert hook["type"] == "command"
                assert "args" not in hook, "the capture hooks are shell commands"
                results.append(
                    subprocess.run(
                        hook["command"],
                        shell=True,
                        input=json.dumps(hook_input) if stdin is None else stdin,
                        capture_output=True,
                        text=True,
                        cwd=self.project,
                        env=self.environment(env or {}),
                        timeout=hook.get("timeout", 600),
                        check=False,
                    )
                )
        return results


@pytest.fixture
def vm_hooks(tmp_path: Path) -> VmHooks:
    return VmHooks(tmp_path / "vm")
