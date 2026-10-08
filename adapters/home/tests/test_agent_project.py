"""Rendering the agent project: a filled-in copy of `agent/` for one instance.

The source project is shared by both instances and names none of them; a rendered copy carries
the owner and the instance paths in CLAUDE.md and .claude/settings.json, and nothing else
differs.
"""

import json
from pathlib import Path

import pytest

from pa_core.owner import Owner
from pa_home.agent_project import AGENT_PROJECT, AgentProjectError, render_agent_project
from pa_home.cli import main

OWNER = Owner(
    name="Argus McNevans",
    email_addresses=("argus@example.com", "Argus.McNevans@example.org"),
    other_names=("Gus",),
)
SETTINGS = Path(".claude") / "settings.json"


@pytest.fixture
def instance(tmp_path: Path) -> dict[str, Path]:
    return {
        "target": tmp_path / "rendered",
        "l0": tmp_path / "data" / "l0",
        "scratch": tmp_path / "scratch",
    }


def _render(instance: dict[str, Path], owner: Owner = OWNER) -> Path:
    render_agent_project(
        instance["target"], owner=owner, l0=instance["l0"], scratch=instance["scratch"]
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
    assert settings["sandbox"]["filesystem"]["allowWrite"] == [scratch]
    assert settings["sandbox"]["filesystem"]["denyWrite"] == [str(target), l0]
    assert settings["sandbox"]["filesystem"]["allowRead"] == [str(target), l0, scratch]
    assert "${" not in (target / SETTINGS).read_text()


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
    settings = json.loads((AGENT_PROJECT / SETTINGS).read_text())
    for value in _strings(settings):
        assert not value.startswith("/"), f"settings.json names an absolute path: {value!r}"


def test_the_permission_policy_fences_hold_in_every_permission_mode(instance):
    """The shared policy (roadmap's permissions table, #22): every fence is a deny rule or the
    Bash sandbox, never an ask rule, since the chat client picks the permission mode."""
    settings = json.loads((_render(instance) / SETTINGS).read_text())
    permissions, sandbox = settings["permissions"], settings["sandbox"]

    assert "ask" not in permissions
    assert "defaultMode" not in permissions
    # Web search and fetch (any domain) without a prompt.
    assert {"WebSearch", "WebFetch"} <= set(permissions["allow"])
    # The file tools write nowhere: scratch is written with sandboxed Bash.
    assert {"Edit", "Write", "NotebookEdit"} <= set(permissions["deny"])
    # No MCP servers: none configured, claude.ai connectors off, and every MCP tool denied.
    assert "mcp__*" in permissions["deny"]
    assert settings["disableClaudeAiConnectors"] is True
    assert settings["enableAllProjectMcpServers"] is False
    assert "mcpServers" not in settings
    # Bash runs sandboxed or not at all, with no network, writing only to scratch.
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert "excludedCommands" not in sandbox
    assert sandbox["network"] == {"deniedDomains": ["*"]}
    assert sandbox["filesystem"]["allowWrite"] == [str(instance["scratch"])]
    assert sandbox["filesystem"]["denyRead"] == ["~/"]


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
