"""The assistant VM's fences as the repo holds them: its managed settings (#81's, the policy the
assistant can't change), and how install-assistant-fences.sh puts them in the VM with the
sandbox's packages and AppArmor profile and the assistant's layout. The script runs here on a
stand-in `incus` that records each call and what was piped to it; the isolation check checks
the VM itself."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
MANAGED = DEPLOY / "managed-settings.json"


def lib_values(*names: str) -> list[str]:
    """The values lib.sh gives `names`, an array's words joined by spaces."""
    script = "; ".join(f'echo "${{{name}[*]}}"' for name in names)
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        check=True,
    )
    return result.stdout.splitlines()


def managed() -> dict[str, Any]:
    return json.loads(MANAGED.read_text())


def test_the_web_mcp_and_the_tools_that_schedule_or_reach_out_are_denied():
    deny = set(managed()["permissions"]["deny"])

    assert {"WebSearch", "WebFetch", "mcp__*"} <= deny
    # Remote Control's own tools, which have no mcp__ prefix (#81).
    assert {"SendUserFile", "FetchInboxMessage"} <= deny
    # The tools that schedule a prompt nobody typed, or reach out.
    assert {"CronCreate", "ScheduleWakeup", "RemoteTrigger", "PushNotification"} <= deny
    assert {"Monitor", "Agent", "Task", "SendMessage", "ListAgents", "Skill"} <= deny


def test_the_assistants_own_tools_are_left_to_it():
    [tools] = lib_values("PA_ASSISTANT_TOOLS")
    deny = set(managed()["permissions"]["deny"])

    assert sorted(tools.split()) == ["Bash", "Edit", "Read", "ToolSearch", "Write"]
    assert not set(tools.split()) & deny


def test_only_managed_rules_and_hooks_apply_and_bypass_mode_is_locked_out():
    settings = managed()

    assert settings["allowManagedPermissionRulesOnly"] is True
    assert settings["allowManagedHooksOnly"] is True
    assert settings["permissions"]["disableBypassPermissionsMode"] == "disable"


def test_memory_and_connectors_are_off():
    settings = managed()

    assert settings["autoMemoryEnabled"] is False
    assert settings["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert settings["disableClaudeAiConnectors"] is True
    assert settings["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


def test_bash_runs_sandboxed_writing_only_to_scratch_with_no_escape():
    [scratch] = lib_values("PA_VM_SCRATCH")
    sandbox = managed()["sandbox"]

    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["filesystem"]["allowWrite"] == [scratch]
    assert sandbox["network"] == {"allowedDomains": [], "allowManagedDomainsOnly": True}


def test_the_file_tools_cant_write_the_home_directory_the_project_the_inbox_or_the_code():
    home, project, inbox, repo = lib_values(
        "PA_VM_HOME", "PA_VM_PROJECT", "PA_VM_CHAT_INBOX", "PA_REPO_DIR"
    )
    deny = set(managed()["permissions"]["deny"])

    for path in ("/home", project, "/srv/pa/inbox", "/etc", "/opt", "/usr"):
        assert f"Edit(/{path}/**)" in deny, path
    assert home.startswith("/home/")
    assert inbox.startswith("/srv/pa/inbox/")
    assert repo.startswith("/opt/")


def test_the_credentials_cant_be_read():
    [home] = lib_values("PA_VM_HOME")
    settings = managed()

    for path in (f"{home}/.claude.json", f"{home}/.claude/.credentials.json"):
        assert f"Read(/{path})" in settings["permissions"]["deny"]
        assert path in settings["sandbox"]["filesystem"]["denyRead"]


def test_updates_error_reports_and_feedback_are_off_but_not_nonessential_traffic():
    """Turning off nonessential traffic breaks Remote Control (#81)."""
    env = managed()["env"]

    for var in ("DISABLE_AUTOUPDATER", "DISABLE_ERROR_REPORTING", "DISABLE_FEEDBACK_COMMAND"):
        assert env[var] == "1"
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" not in env


def test_the_sessions_temp_directory_stays_out_of_the_denied_home_directory():
    """The sandbox writes its own temp directory, and a denied one breaks Bash (#81). An eval
    run sets TMPDIR for its throwaway data root, which mustn't move it."""
    assert managed()["env"]["CLAUDE_CODE_TMPDIR"] == "/tmp"


def test_the_capture_hook_runs_the_projects_script_with_the_codes_interpreter():
    """Unchanged from the agent project's settings, until #94 replaces it."""
    project, python = lib_values("PA_VM_PROJECT", "PA_VM_HOOK_PYTHON")

    [group] = managed()["hooks"]["Stop"]
    assert group["hooks"] == [
        {
            "type": "command",
            "command": python,
            "args": [f"{project}/.claude/hooks/capture_exchange.py"],
            "timeout": 30,
        }
    ]
    assert set(managed()["hooks"]) == {"Stop"}


def test_the_home_directorys_claude_md_files_are_excluded_but_not_the_projects_or_evals():
    """A run renders an agent project for each question in the evals' directory (#92)."""
    home, project, evals = lib_values("PA_VM_HOME", "PA_VM_PROJECT", "PA_VM_EVAL_TMP")

    assert managed()["claudeMdExcludes"] == [f"{home}/**"]
    for path in (project, evals):
        assert not path.startswith(f"{home}/")


INSTALL = DEPLOY / "install-assistant-fences.sh"

STAND_IN_INCUS = """\
import json, os, sys
argv = sys.argv[1:]
stdin = sys.stdin.buffer.read() if argv[0] == "exec" and "-s" in argv else b""
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps({"argv": argv, "stdin": stdin.decode()}) + "\\n")
if argv[0] == "exec" and "dpkg-query" in " ".join(argv):
    sys.exit(int(os.environ["PACKAGES_MISSING"]))
"""


def install(tmp_path: Path, *, packages_missing: bool = False) -> list[dict[str, Any]]:
    """Runs install-assistant-fences.sh on the stand-in `incus`; returns its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    incus = bin_dir / "incus"
    incus.write_text(f"#!{sys.executable}\n{STAND_IN_INCUS}")
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/bin/bash\nexec "$@"\n')
    for script in (incus, sudo):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(INSTALL)],
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(calls),
            "PACKAGES_MISSING": "1" if packages_missing else "0",
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in calls.read_text().splitlines()]


def pushed(calls: list[dict[str, Any]], to: str) -> tuple[str, list[str]]:
    """The file pushed to `to` in the VM, and the options it was pushed with."""
    [argv] = [c["argv"] for c in calls if c["argv"][:2] == ["file", "push"] and c["argv"][-1] == to]
    return argv[-2], argv[2:-2]


def test_the_managed_settings_go_in_as_the_repo_has_them_roots_and_mode_0644(tmp_path: Path):
    source, options = pushed(install(tmp_path), "pa-home/etc/claude-code/managed-settings.json")

    assert Path(source) == MANAGED
    assert options == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]


def test_the_sandboxs_apparmor_profile_goes_in_roots_and_replaces_ubuntus(tmp_path: Path):
    calls = install(tmp_path)

    source, options = pushed(calls, "pa-home/etc/apparmor.d/pa-bwrap")
    assert Path(source) == DEPLOY / "apparmor" / "pa-bwrap"
    assert options == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]
    [load] = [c for c in calls if "apparmor_parser -r" in " ".join(c["argv"])]
    assert "/etc/apparmor.d/bwrap-userns-restrict" in load["argv"]
    assert "/etc/apparmor.d/pa-bwrap" in load["argv"]


def test_the_sandboxs_packages_are_installed_only_when_missing(tmp_path: Path):
    def installs(calls: list[dict[str, Any]]) -> list[list[str]]:
        return [c["argv"] for c in calls if "apt-get" in " ".join(c["argv"])]

    assert installs(install(tmp_path / "present")) == []
    update, apt = installs(install(tmp_path / "missing", packages_missing=True))
    assert update[-2:] == ["update", "-q"]
    assert apt[-3:] == ["bubblewrap", "socat", "apparmor"]


def test_the_oss_own_updates_and_clock_sync_are_masked_so_nothing_knocks_on_the_fence(
    tmp_path: Path,
):
    calls = install(tmp_path)

    [mask] = [c["argv"] for c in calls if "mask" in c["argv"]]
    [stop] = [c["argv"] for c in calls if "stop" in c["argv"]]
    assert mask[:5] == ["exec", "pa-home", "--", "systemctl", "mask"]
    assert stop[:5] == ["exec", "pa-home", "--", "systemctl", "stop"]
    for unit in (
        "apt-daily.timer",
        "apt-daily-upgrade.timer",
        "unattended-upgrades.service",
        "systemd-timesyncd.service",
    ):
        assert unit in mask
        assert unit in stop
    source, options = pushed(calls, "pa-home/etc/apt/apt.conf.d/99-pa-no-periodic")
    assert options == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]
    periodic = Path(source).read_text()
    assert 'APT::Periodic::Update-Package-Lists "0";' in periodic
    assert 'APT::Periodic::Unattended-Upgrade "0";' in periodic


def test_the_assistants_layout_is_made_as_root(tmp_path: Path):
    [layout] = [
        c for c in install(tmp_path) if c["stdin"] == (DEPLOY / "assistant-layout.sh").read_text()
    ]

    assert layout["argv"][:4] == ["exec", "pa-home", "--", "bash"]
    assert layout["argv"][-4:] == [
        "assistant",
        "/home/assistant",
        "/srv/pa/scratch",
        "/srv/pa/eval",
    ]


# The isolation check's reading of what a session in the VM printed (claude -p --output-format
# stream-json --verbose).


def lib(script: str, *, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        check=False,
    )


def printed(*events: dict[str, Any], tools: list[str] | None = None) -> str:
    """What a session printed: its init message, `events`, and its result."""
    init = {
        "type": "system",
        "subtype": "init",
        "tools": tools or ["Bash", "Edit", "Read", "ToolSearch", "Write"],
        "mcp_servers": [],
        "permissionMode": "default",
    }
    result = {"type": "result", "subtype": "success", "is_error": False, "result": "DONE"}
    return "".join(json.dumps(event) + "\n" for event in (init, *events, result))


def ran(command: str, output: str, *, is_error: bool = False) -> tuple[dict, dict]:
    """A Bash call of `command`, and its result."""
    call = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command}}
    result = {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": output,
        "is_error": is_error,
    }
    return (
        {"type": "assistant", "message": {"content": [call]}},
        {"type": "user", "message": {"content": [result]}},
    )


DENIED = ("/home/assistant", "/srv/pa/inbox/assistant_chat", "/srv/pa/agent")
PROBE_ARGS = "/srv/pa/scratch " + " ".join(DENIED)


def probe_problems(output: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    command = lib(f"sandbox_probe {PROBE_ARGS}").stdout.strip()
    return lib(
        f"sandbox_probe_problems {PROBE_ARGS}", stdin=printed(*ran(command, output, **kwargs))
    )


SANDBOXED = (
    "NoNewPrivs:\t1\n"
    "wrote /srv/pa/scratch\n"
    "refused /home/assistant\n"
    "refused /srv/pa/inbox/assistant_chat\n"
    "refused /srv/pa/agent\n"
    "sudo refused\n"
)


def test_the_probe_reports_where_it_could_write_its_privileges_and_sudo(tmp_path: Path):
    """The probe's command, run here outside the sandbox, over a writable directory and one it
    can't write: it reports each, and leaves nothing behind."""
    scratch, locked = tmp_path / "scratch", tmp_path / "locked"
    scratch.mkdir()
    locked.mkdir(mode=0o555)
    command = lib(f"sandbox_probe {scratch} {locked}").stdout

    output = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, check=True
    ).stdout.splitlines()

    assert output[0].startswith("NoNewPrivs:\t")
    assert output[1:3] == [f"wrote {scratch}", f"refused {locked}"]
    assert output[3] in ("sudo ran", "sudo refused")
    assert list(scratch.iterdir()) == []


def test_the_probe_holds_no_shell_variable_for_claude_code_to_refuse():
    """Claude Code 2.1.308 won't run a command whose variables it can't check before it runs, so a
    headless session refused the probe on the live VM (#91)."""
    command = lib(f"sandbox_probe {PROBE_ARGS}").stdout

    assert "$" not in command


def test_a_probe_that_ran_sandboxed_has_no_problems():
    result = probe_problems(SANDBOXED)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_probe_that_wrote_outside_scratch_or_ran_sudo_fails():
    output = (
        SANDBOXED.replace("refused /srv/pa/agent", "wrote /srv/pa/agent")
        .replace("sudo refused", "sudo ran")
        .replace("wrote /srv/pa/scratch", "refused /srv/pa/scratch")
    )

    assert probe_problems(output).stdout.splitlines() == [
        "the sandboxed shell couldn't write /srv/pa/scratch",
        "the sandboxed shell wrote /srv/pa/agent",
        "sudo ran in the sandboxed shell",
    ]


def test_a_probe_that_didnt_run_sandboxed_fails():
    """Ubuntu's AppArmor profile back: the session starts, and every command fails (#81)."""
    failed = probe_problems(
        "apply-seccomp: write /proc/self/setgroups: Permission denied", is_error=True
    )
    unsandboxed = probe_problems(SANDBOXED.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"))

    assert failed.stdout.splitlines()[0].startswith("Bash didn't run in the sandbox")
    assert unsandboxed.stdout.splitlines() == ["Bash didn't run in the sandbox: NoNewPrivs:\t0"]


def test_a_session_that_never_ran_the_probe_is_an_error():
    result = lib(f"sandbox_probe_problems {PROBE_ARGS}", stdin=printed())

    assert result.returncode != 0
    assert "never ran the probe" in result.stderr


def test_a_session_has_exactly_the_assistants_tools():
    tools = lib('echo "${PA_ASSISTANT_TOOLS[@]}"').stdout.split()

    exact = lib('session_fence_problems "${PA_ASSISTANT_TOOLS[@]}"', stdin=printed(tools=tools))
    more = lib(
        'session_fence_problems "${PA_ASSISTANT_TOOLS[@]}"',
        stdin=printed(tools=[*tools, "WebFetch", "SendUserFile"]),
    )

    assert exact.returncode == 0, exact.stderr
    assert exact.stdout == ""
    assert more.stdout.splitlines() == ["it has the tool SendUserFile", "it has the tool WebFetch"]
