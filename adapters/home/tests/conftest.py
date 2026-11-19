"""Stand-ins shared by the home tests: the home VM's capture hooks, and the librarian's `claude`.

The home VM's capture hooks, as Claude Code fires them there, standing in a test's directories.

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
import textwrap
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


def write_stand_in_claude(directory: Path) -> Path:
    """The stand-in `claude` (test_librarian_run.py's docstring says what it does), in
    `directory`, which also holds its log (`sessions.jsonl`) and its script (`script.json`)."""
    script = directory / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, os, re, sys, time
            from pathlib import Path
            LOG = Path({str(directory / "sessions.jsonl")!r})
            SCRIPT = Path({str(directory / "script.json")!r})
            """
        )
        + textwrap.dedent(
            """\
            argv = sys.argv[1:]
            prompt = sys.stdin.read()
            settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
            wiki = Path(settings["permissions"]["additionalDirectories"][1])
            phase = re.search(r"^Phase: (\\w+)$", prompt, re.M)[1]
            listing = re.search(r"^```json\\n(.*?)^```$", prompt, re.M | re.S)
            episodes = json.loads(listing[1]) if listing else []
            table = re.search(r"^## Identity table$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            identity = json.loads(table[1]) if table else None
            found = re.search(r"^## Lint warnings$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            warnings = json.loads(found[1]) if found else None
            found = re.search(r"^## Lint errors$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            errors = json.loads(found[1]) if found else None
            phase_inputs = prompt[:table.start()] if table else prompt
            inputs = [json.loads(block) for block in
                      re.findall(r"^```json\\n(.*?)^```$", phase_inputs, re.M | re.S)]
            claude_md = Path("CLAUDE.md")
            with LOG.open("a") as log:
                log.write(json.dumps({
                    "phase": phase, "argv": argv, "prompt": prompt, "cwd": os.getcwd(),
                    "claude_md": claude_md.read_text() if claude_md.exists() else None,
                    "settings": settings, "episodes": episodes, "identity": identity,
                    "warnings": warnings, "errors": errors, "inputs": inputs,
                }) + "\\n")
            script = json.loads(SCRIPT.read_text()) if SCRIPT.exists() else {}

            def write(path, text, mode="w"):
                target = wiki / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open(mode) as file:
                    file.write(text)

            default = {"observation": [{"observe": True}]}
            # A resumed session follows `<phase>_resume`; a new one, `<phase>`.
            key = f"{phase}_resume" if "--resume" in argv else phase
            for action in script.get(key, default.get(key, [])):
                if action.get("when") not in (None, *(e.get("subject") for e in episodes)):
                    continue
                if "observe" in action:
                    for episode in episodes:
                        slugs = action.get("links", {}).get(episode["subject"], ["someone"])
                        links = " and ".join(f"[[{slug}]]" for slug in slugs)
                        write(f"days/{episode['day']}.md",
                              f"- {episode['kind']} about {episode['subject']} with "
                              f"{links} [ep:{episode['id']}]\\n", "a")
                elif "delete" in action:
                    (wiki / action["delete"]).unlink()
                elif "write" in action:
                    write(action["write"], action["text"])
                elif "append" in action:
                    write(action["append"], action["text"], "a")
                elif "symlink" in action:
                    (wiki / action["symlink"]).parent.mkdir(parents=True, exist_ok=True)
                    (wiki / action["symlink"]).symlink_to(action["to"])
                elif "mkdir" in action:
                    (wiki / action["mkdir"]).mkdir(parents=True, exist_ok=True)
                elif "sleep" in action:
                    time.sleep(action["sleep"])
                elif "fail" in action:
                    print(json.dumps({"type": "result", "subtype": "error_during_execution",
                                      "is_error": True, "result": action["fail"]}))
                    sys.exit(1)
            print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": "Done.", "session_id": "session-1",
                              "total_cost_usd": 0.0421, "num_turns": 6,
                              "duration_ms": 18250}))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture
def stand_in_claude(tmp_path: Path) -> Path:
    """The librarian's sessions' stand-in `claude`, logging and scripted in `tmp_path`."""
    return write_stand_in_claude(tmp_path)
