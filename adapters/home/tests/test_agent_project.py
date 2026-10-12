"""Rendering the agent project: a filled-in copy of `agent/` for one instance.

The source project is shared by both instances and names none of them; a rendered copy carries
the owner and the instance paths in CLAUDE.md and .claude/settings.json, and nothing else
differs.
"""

import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import anyio
import pytest
from mcp import Client, StdioServerParameters

from pa_core.owner import Owner
from pa_home.agent_project import AGENT_PROJECT, AgentProjectError, render_agent_project
from pa_home.cli import main
from pa_home.graph import extensions

OWNER = Owner(
    name="Argus McNevans",
    email_addresses=("argus@example.com", "Argus.McNevans@example.org"),
    other_names=("Gus",),
)
SETTINGS = Path(".claude") / "settings.json"


MCP_CONFIG = Path(".mcp.json")
CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture
def instance(tmp_path: Path) -> dict[str, Path]:
    return {
        "target": tmp_path / "rendered",
        "l0": tmp_path / "data" / "l0",
        "scratch": tmp_path / "scratch",
        "graph": tmp_path / "data" / "graph",
    }


def _render(instance: dict[str, Path], owner: Owner = OWNER) -> Path:
    render_agent_project(
        instance["target"],
        owner=owner,
        l0=instance["l0"],
        scratch=instance["scratch"],
        graph=instance["graph"],
        time_zone=CHICAGO,
    )
    return instance["target"]


def _files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def test_the_rendered_claude_md_names_the_owner_and_where_l0_and_scratch_are(instance):
    claude_md = (_render(instance) / "CLAUDE.md").read_text()

    for value in (
        "Argus McNevans",
        "Gus",
        "`argus@example.com`, `argus.mcnevans@example.org`",
        str(instance["l0"]),
        str(instance["scratch"]),
    ):
        assert value in claude_md
    assert "${" not in claude_md, "a placeholder was left unfilled"


def test_the_rendered_settings_hold_the_instance_paths(instance):
    target = _render(instance)
    settings = json.loads((target / SETTINGS).read_text())

    l0, scratch = str(instance["l0"]), str(instance["scratch"])
    assert settings["permissions"]["additionalDirectories"] == [l0, scratch]
    assert "${" not in (target / SETTINGS).read_text()


def test_the_rendered_capture_hook_runs_the_projects_hook_script_with_the_rendering_python(
    instance,
):
    target = _render(instance)
    settings = json.loads((target / SETTINGS).read_text())

    [group] = settings["hooks"]["Stop"]
    [hook] = group["hooks"]
    # Exec form (command and args, no shell), so no path needs quoting.
    assert hook["command"] == sys.executable
    assert hook["args"] == [str(target / ".claude" / "hooks" / "capture_exchange.py")]
    assert (target / ".claude" / "hooks" / "capture_exchange.py").is_file()


def test_the_rendered_project_declares_the_pa_server_and_no_other(instance):
    target = _render(instance)

    config = json.loads((target / MCP_CONFIG).read_text())

    assert list(config["mcpServers"]) == ["pa"]
    assert config["mcpServers"]["pa"]["type"] == "stdio"
    assert "${" not in (target / MCP_CONFIG).read_text()


def test_the_declared_pa_server_serves_the_instances_graph_over_stdio(instance):
    instance["graph"].mkdir(parents=True)
    extensions.ensure(extensions.extensions_dir(), extensions.FTS)
    pa = json.loads((_render(instance) / MCP_CONFIG).read_text())["mcpServers"]["pa"]
    # As Claude Code starts it: the declared command, in the project, with the session's
    # environment (no data root configured).
    environment = {name: value for name, value in os.environ.items() if name != "PA_DATA_DIR"}
    server = StdioServerParameters(
        command=pa["command"], args=pa["args"], env=environment, cwd=instance["target"]
    )

    async def go() -> tuple[set[str], str]:
        async with Client(server, mode="legacy") as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("find", {"name": "Dave"})
        assert result.is_error
        return tools, " ".join(getattr(block, "text", "") for block in result.content)

    tools, error = anyio.run(go)

    assert {"find", "statements", "timeline", "schema", "query"} <= tools
    # Nothing is published in the instance's graph directory yet, and the server says where it
    # looked.
    assert f"No claims graph has been published at {instance['graph']}" in error


def test_rendered_without_a_graph_the_project_declares_no_mcp_server(tmp_path: Path, instance):
    """Files alone: the v0.11 configuration, which evals still compare against."""
    target = instance["target"]

    render_agent_project(target, owner=OWNER, l0=instance["l0"], scratch=instance["scratch"])

    assert not (target / MCP_CONFIG).exists()
    assert (target / "CLAUDE.md").is_file() and (target / SETTINGS).is_file()


def test_tools_hidden_from_a_rendering_are_denied_by_name_and_the_rest_of_the_policy_stays(
    instance,
):
    """How a configuration keeps some of the PA server's tools from the assistant: a deny rule
    for one MCP tool removes it from the session, and the server's other tools stay."""
    target = instance["target"]
    whole = json.loads((_render(instance) / SETTINGS).read_text())
    narrowed_target = target.with_name("narrowed")

    render_agent_project(
        narrowed_target,
        owner=OWNER,
        l0=instance["l0"],
        scratch=instance["scratch"],
        graph=instance["graph"],
        time_zone=CHICAGO,
        hidden_tools={"timeline", "statements"},
    )

    narrowed = json.loads((narrowed_target / SETTINGS).read_text())
    assert narrowed["permissions"]["deny"] == [
        *whole["permissions"]["deny"],
        "mcp__pa__statements",
        "mcp__pa__timeline",
    ]
    assert "mcp__pa" in narrowed["permissions"]["allow"]
    narrowed["permissions"]["deny"] = whole["permissions"]["deny"]
    # The hook's path names each rendering's own directory; nothing else differs.
    del narrowed["hooks"], whole["hooks"]
    assert narrowed == whole
    assert (narrowed_target / MCP_CONFIG).read_text() == (target / MCP_CONFIG).read_text()


def test_hiding_tools_needs_the_graph_their_server_reads(instance):
    with pytest.raises(AgentProjectError, match="graph"):
        render_agent_project(
            instance["target"],
            owner=OWNER,
            l0=instance["l0"],
            scratch=instance["scratch"],
            hidden_tools={"timeline"},
        )


def test_a_rendered_project_differs_from_the_source_only_where_instance_values_go(instance):
    target = _render(instance)

    assert _files(target) == _files(AGENT_PROJECT)
    for path in _files(AGENT_PROJECT):
        source_lines = (AGENT_PROJECT / path).read_text().splitlines()
        rendered_lines = (target / path).read_text().splitlines()
        assert len(rendered_lines) == len(source_lines), path
        for source, rendered in zip(source_lines, rendered_lines, strict=True):
            if source != rendered:
                assert "${" in source, f"{path}: {source!r} changed with no placeholder"
                assert "${" not in rendered, f"{path}: {rendered!r} left a placeholder"


def test_the_source_project_names_no_owner_and_no_instance_path():
    for path in _files(AGENT_PROJECT):
        text = (AGENT_PROJECT / path).read_text()
        for value in ("Argus", "McNevans", "@example."):
            assert value not in text, f"{path} names {value!r}"
        # The PA MCP server's `schema` tool carries the Cypher examples, so none go stale here.
        assert not re.search(r"\b(MATCH|RETURN)\b", text), f"{path} holds a Cypher example"
        # Nothing engine-specific: the graph's engine and query language can change under it.
        for engine_word in ("Cypher", "Ladybug", "lbug"):
            assert engine_word.lower() not in text.lower(), f"{path} names {engine_word!r}"
    settings = json.loads((AGENT_PROJECT / SETTINGS).read_text())
    for value in _strings(settings):
        assert not value.startswith("/"), f"settings.json names an absolute path: {value!r}"


def test_the_permission_policy_leaves_the_fences_to_the_vm_and_holds_in_every_mode(instance):
    """The shared policy (roadmap's permissions table, #22). Isolation is the VM's: its
    tailnet rules, the host firewall and the read-only L0 mount. Inside it the assistant has
    its normal tools, so there is no Bash sandbox and no file-tool deny rule. What the settings
    do fence is a deny rule or the MCP server allowlist, never an ask rule, since the chat
    client picks the permission mode."""
    settings = json.loads((_render(instance) / SETTINGS).read_text())
    permissions = settings["permissions"]

    assert "ask" not in permissions
    assert "defaultMode" not in permissions
    assert "sandbox" not in settings
    # Web search and fetch (any domain) without a prompt.
    assert {"WebSearch", "WebFetch"} <= set(permissions["allow"])
    # The normal tools stay: no tool is denied outright.
    assert [rule for rule in permissions["deny"] if "(" not in rule] == []
    # The PA MCP server's tools run without a prompt. (A deny rule on the other MCP tools would
    # deny the PA server's too: deny wins over allow.)
    assert "mcp__pa" in permissions["allow"]
    # Only the PA server may run at all, whatever declares the others: other servers in
    # .mcp.json, user-scoped servers and claude.ai connectors are blocked before they start.
    assert settings["allowedMcpServers"] == [{"serverName": "pa"}]
    assert settings["disableClaudeAiConnectors"] is True
    # Approved without asking where Claude Code would ask (Remote Control has nobody to ask),
    # and nothing in .mcp.json approved wholesale.
    assert settings["enabledMcpjsonServers"] == ["pa"]
    assert settings["enableAllProjectMcpServers"] is False
    assert "mcpServers" not in settings
    # The Stop hook captures each exchange (see the capture hook test).
    assert settings["hooks"]["Stop"]


def test_two_owners_get_their_own_rendered_projects(tmp_path: Path, instance):
    other = Owner(name="Wren Okafor", email_addresses=("wren@example.org",))

    claude_md = (_render(instance, other) / "CLAUDE.md").read_text()

    assert "Wren Okafor" in claude_md and "`wren@example.org`" in claude_md
    assert "Also called: (none)" in claude_md
    assert "Argus" not in claude_md


@pytest.mark.parametrize(
    "path", ['/data/"quoted"/l0', "/data/back\\slash/l0", "/data/dollar$sign/l0"]
)
def test_paths_with_json_or_template_characters_render_intact(tmp_path: Path, path: str):
    target = tmp_path / "rendered"

    render_agent_project(target, owner=OWNER, l0=Path(path), scratch=tmp_path / "scratch")

    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"][0] == path
    assert f"`{path}`" in (target / "CLAUDE.md").read_text()


def test_rendering_refuses_a_target_that_already_has_files(instance):
    instance["target"].mkdir()
    (instance["target"] / "notes.txt").write_text("keep me")

    with pytest.raises(AgentProjectError, match="not empty"):
        _render(instance)

    assert (instance["target"] / "notes.txt").read_text() == "keep me"
    assert _files(instance["target"]) == [Path("notes.txt")]


def test_rendering_into_an_empty_directory_is_fine(instance):
    instance["target"].mkdir()

    _render(instance)

    assert (instance["target"] / "CLAUDE.md").exists()


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.fixture
def owner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PA_DATA_DIR", raising=False)
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", "Gus")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")


def _render_command(instance: dict[str, Path]) -> list[str]:
    return [
        "agent-project",
        "render",
        str(instance["target"]),
        "--l0",
        str(instance["l0"]),
        "--scratch",
        str(instance["scratch"]),
    ]


def test_the_render_command_fills_in_the_configured_owner_and_the_given_paths(
    instance, owner_env, capsys: pytest.CaptureFixture[str]
):
    assert main(_render_command(instance)) == 0

    target = instance["target"]
    claude_md = (target / "CLAUDE.md").read_text()
    assert "Argus McNevans" in claude_md and "`argus@example.com`" in claude_md
    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"] == [
        str(instance["l0"].resolve()),
        str(instance["scratch"].resolve()),
    ]
    assert str(target.resolve()) in capsys.readouterr().out


def test_the_render_command_points_the_pa_server_at_the_given_graph(instance, owner_env):
    command = [*_render_command(instance), "--graph", str(instance["graph"])]

    assert main(command) == 0

    pa = json.loads((instance["target"] / MCP_CONFIG).read_text())["mcpServers"]["pa"]
    assert str(instance["graph"].resolve()) in pa["args"]
    # Dates the assistant gives without an offset are read in the instance's time zone.
    assert pa["args"][-2:] == ["--time-zone", "America/Chicago"]


def test_the_render_command_with_a_graph_needs_the_instances_time_zone(
    instance, owner_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("PA_TIME_ZONE")

    assert main([*_render_command(instance), "--graph", str(instance["graph"])]) != 0

    assert "PA_TIME_ZONE" in capsys.readouterr().err
    assert not instance["target"].exists()


def test_the_render_command_without_a_graph_declares_no_mcp_server(instance, owner_env):
    assert main(_render_command(instance)) == 0

    assert not (instance["target"] / MCP_CONFIG).exists()


def test_the_render_command_needs_the_owner(
    instance, owner_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv("PA_OWNER_NAME")

    assert main(_render_command(instance)) != 0

    assert "PA_OWNER_NAME" in capsys.readouterr().err
    assert not instance["target"].exists()


def test_the_render_command_refuses_a_target_inside_the_repository(
    instance, owner_env, capsys: pytest.CaptureFixture[str]
):
    """A rendered project names the owner, so it must never land where it could be committed."""
    instance["target"] = AGENT_PROJECT.parent / ".scratch" / "rendered-agent-project"

    assert main(_render_command(instance)) != 0

    assert "inside the repository" in capsys.readouterr().err
    assert not instance["target"].exists()


def test_the_render_command_refuses_a_target_with_files(
    instance, owner_env, capsys: pytest.CaptureFixture[str]
):
    instance["target"].mkdir()
    (instance["target"] / "notes.txt").write_text("keep me")

    assert main(_render_command(instance)) != 0

    assert "not empty" in capsys.readouterr().err
