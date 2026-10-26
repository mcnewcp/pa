"""The deploy scripts' view of the librarian's VM: which VM a script works on, how a run's shares
are checked, which addresses count as the API's, and how the check reads a session's fences from
what `claude -p --output-format stream-json --verbose` printed. They run here on stand-in input;
the check itself runs on the devbox (check-librarian.sh)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pa_home.claude_session import FILE_TOOLS as SESSION_TOOLS

LIB = Path(__file__).resolve().parents[1] / "lib.sh"
SETTINGS_PATH = "librarian/.claude/settings.json"


def lib(script: str, *, stdin: str = "", home: Path | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home or Path("/nonexistent"))}
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ("", "pa-home pa pabr0 assistant profile.yaml |"),
        ("main", "pa-home pa pabr0 assistant profile.yaml |main"),
        ("--vm assistant v1", "pa-home pa pabr0 assistant profile.yaml |v1"),
        ("--vm librarian", "pa-librarian pa-librarian palib0 librarian profile.yaml |"),
        ("--vm=librarian a b", "pa-librarian pa-librarian palib0 librarian profile.yaml |a b"),
    ],
)
def test_a_script_works_on_the_assistants_vm_unless_given_another(args: str, expected: str):
    result = lib(
        f"take_vm_option {args}; "
        'echo "$PA_VM $PA_PROFILE $PA_BRIDGE $PA_USER ${PA_VM_PROFILE_FILE##*/} |${ARGS[*]}"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_the_librarian_has_a_profile_of_its_own():
    result = lib('take_vm_option --vm librarian; echo "$PA_VM_PROFILE_FILE"')

    assert result.stdout.strip() == str(LIB.parent / "librarian" / "profile.yaml")


def test_an_unknown_vm_is_refused():
    result = lib("take_vm_option --vm research")

    assert result.returncode != 0
    assert "--vm takes assistant or librarian" in result.stderr


def disk(source: str, path: str, readonly: bool | None = None) -> dict[str, str]:
    device = {"type": "disk", "source": source, "path": path}
    if readonly is not None:
        device["readonly"] = str(readonly).lower()
    return device


A_RUN = (
    "lib-l0|/data/l0|/srv/pa/l0|true",
    "lib-project|/tmp/run/project|/srv/pa/librarian|true",
    "lib-wiki|/data/wiki|/srv/pa/wiki|false",
)


def a_runs_devices() -> dict[str, dict[str, str]]:
    return {
        "root": {"type": "disk", "pool": "pa", "path": "/"},
        "eth0": {"type": "nic", "network": "palib0"},
        "lib-l0": disk("/data/l0", "/srv/pa/l0", True),
        "lib-project": disk("/tmp/run/project", "/srv/pa/librarian", True),
        "lib-wiki": disk("/data/wiki", "/srv/pa/wiki", False),
    }


def share_problems(devices: dict[str, Any]) -> list[str]:
    instance = {"name": "pa-librarian", "expanded_devices": devices}
    shares = " ".join(f"'{share}'" for share in A_RUN)
    result = lib(f"share_problems {shares}", stdin=json.dumps(instance))
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_a_runs_shares_as_started_have_no_problems():
    assert share_problems(a_runs_devices()) == []


def test_a_read_only_wiki_is_a_problem_for_the_librarian():
    devices = a_runs_devices()
    devices["lib-wiki"]["readonly"] = "true"

    assert share_problems(devices) == ["/data/wiki is shared read-only"]


def test_a_writable_project_is_a_problem():
    devices = a_runs_devices()
    del devices["lib-project"]["readonly"]

    assert share_problems(devices) == ["/tmp/run/project is shared writable"]


def test_the_wikis_git_directory_shared_is_a_problem():
    devices = a_runs_devices()
    devices["lib-git"] = disk("/data/wiki.git", "/srv/pa/wiki.git", True)

    assert share_problems(devices) == ["/data/wiki.git is shared too, at /srv/pa/wiki.git"]


def test_only_addresses_in_the_apis_published_range_count_as_the_apis():
    addresses = "160.79.104.10\n160.79.105.255\n160.79.106.1\n8.8.8.8\nnot-an-address\n"

    result = lib("in_api_range", stdin=addresses)

    assert result.stdout.splitlines() == ["160.79.104.10", "160.79.105.255"]


FILE_TOOLS = ["Edit", "Glob", "Grep", "Read", "Write"]


def test_every_list_of_the_librarians_file_tools_names_the_same_five():
    """The tools a session is started with (`--tools`), the ones the librarian project's
    settings allow, the ones the check expects, and none the VM's managed settings deny."""
    deploy = LIB.parent
    project_settings = json.loads((deploy.parents[1] / SETTINGS_PATH).read_text())
    managed = json.loads((deploy / "librarian" / "managed-settings.json").read_text())
    in_lib = lib('echo "${PA_LIBRARIAN_TOOLS[@]}"').stdout.split()

    assert sorted(SESSION_TOOLS) == FILE_TOOLS
    assert sorted(project_settings["permissions"]["allow"]) == FILE_TOOLS
    assert sorted(in_lib) == FILE_TOOLS
    assert not set(FILE_TOOLS) & set(managed["permissions"]["deny"])


def session(
    *,
    tools: list[str] = FILE_TOOLS,
    mcp_servers: list[dict[str, str]] | None = None,
    mode: str = "default",
    result: dict[str, Any] | None = None,
) -> str:
    """What a session printed: its init message, a reply, and its result."""
    events = [
        {
            "type": "system",
            "subtype": "init",
            "tools": tools,
            "mcp_servers": mcp_servers or [],
            "permissionMode": mode,
        },
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "OK"}]}},
        result or {"type": "result", "subtype": "success", "is_error": False, "result": "OK"},
    ]
    return "".join(json.dumps(event) + "\n" for event in events)


def fence_problems(printed: str) -> subprocess.CompletedProcess:
    return lib("session_fence_problems", stdin=printed)


def test_a_session_with_exactly_the_file_tools_passes():
    result = fence_problems(session(tools=list(reversed(FILE_TOOLS))))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_any_tool_beyond_the_file_tools_fails_even_one_no_list_names():
    result = fence_problems(session(tools=[*FILE_TOOLS, "Bash", "SomeNewTool", "mcp__stub__x"]))

    assert result.stdout.splitlines() == [
        "it has the tool Bash",
        "it has the tool SomeNewTool",
        "it has the tool mcp__stub__x",
    ]


def test_a_missing_file_tool_fails():
    assert fence_problems(session(tools=["Read"])).stdout.splitlines() == [
        "it lacks the tool Edit",
        "it lacks the tool Glob",
        "it lacks the tool Grep",
        "it lacks the tool Write",
    ]


def test_a_connected_mcp_server_or_bypass_mode_fails():
    printed = session(
        mcp_servers=[{"name": "stub", "status": "connected"}, {"name": "x", "status": "failed"}],
        mode="bypassPermissions",
    )

    assert fence_problems(printed).stdout.splitlines() == [
        "the MCP server stub connected",
        "it runs in bypass mode",
    ]


def test_a_session_that_never_started_or_failed_is_an_error():
    never = fence_problems('{"type": "result", "is_error": true, "result": "Not logged in"}\n')
    failed = fence_problems(
        session(result={"type": "result", "is_error": True, "result": "API Error: 401"})
    )

    assert never.returncode != 0
    assert "never started" in never.stderr
    assert failed.returncode != 0
    assert "API Error: 401" in failed.stderr


ACL = LIB.parent / "librarian" / "egress-acl.json"


def as_incus_reports(acl: dict[str, Any]) -> dict[str, Any]:
    """The ACL as `incus query` prints it: every rule field present, empty if unset."""
    fields = ("action", "source", "destination", "protocol", "source_port", "destination_port")
    fields += ("icmp_type", "icmp_code", "description", "state")
    return {
        **acl,
        "name": "pa-librarian-egress",
        "config": {},
        "used_by": ["/1.0/instances/pa-librarian"],
        "egress": [{f: rule.get(f, "") for f in fields} for rule in acl["egress"]],
    }


def acl_differs(reported: dict[str, Any]) -> bool:
    result = lib(f'acl_differs "{ACL}"', stdin=json.dumps(reported))
    assert result.returncode in (0, 1), result.stderr
    return result.returncode == 0


def test_the_acl_as_incus_reports_it_is_the_repos():
    assert not acl_differs(as_incus_reports(json.loads(ACL.read_text())))


def test_a_changed_or_extra_rule_differs_from_the_repos():
    acl = json.loads(ACL.read_text())
    widened = {**acl, "egress": [{**acl["egress"][0], "destination": "0.0.0.0/0"}]}
    extra = {**acl, "egress": [*acl["egress"], {"action": "allow", "state": "enabled"}]}

    assert acl_differs(as_incus_reports(widened))
    assert acl_differs(as_incus_reports(extra))
