"""Rendering the agent project: a filled-in copy of `agent/` for one instance.

The source project is shared by both instances and names none of them; a rendered copy carries
the owner and the instance paths in CLAUDE.md and .claude/settings.json, and nothing else
differs.
"""

import json
from pathlib import Path

import pytest

from pa_core.owner import Owner
from pa_home.agent_project import (
    AGENT_PROJECT,
    WIKI_SECTION,
    AgentProjectError,
    render_agent_project,
)
from pa_home.cli import main
from pa_home.librarian_project import WIKI_SCHEMA

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


def _render(instance: dict[str, Path], owner: Owner = OWNER, *, wiki: Path | None = None) -> Path:
    render_agent_project(
        instance["target"], owner=owner, l0=instance["l0"], scratch=instance["scratch"], wiki=wiki
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


def test_the_rendered_project_holds_the_capture_hooks_script_but_its_settings_run_no_hook(
    instance,
):
    """The hook moved into the VM's managed settings, the only hooks that run there (#92); it
    runs the rendered project's copy of the script."""
    target = _render(instance)
    settings = json.loads((target / SETTINGS).read_text())

    assert "hooks" not in settings
    assert (target / ".claude" / "hooks" / "capture_exchange.py").is_file()


def test_a_rendered_project_differs_from_the_source_only_where_instance_values_go(instance):
    """Without a wiki, too: the project the assistant ran in before the wiki."""
    target = _render(instance)

    source_files = [path for path in _files(AGENT_PROJECT) if path != Path(WIKI_SECTION)]
    assert _files(target) == source_files
    for path in source_files:
        source_lines = (AGENT_PROJECT / path).read_text().splitlines()
        rendered_lines = (target / path).read_text().splitlines()
        assert len(rendered_lines) == len(source_lines), path
        for source, rendered in zip(source_lines, rendered_lines, strict=True):
            if source != rendered:
                assert "${" in source, f"{path}: {source!r} changed with no placeholder"
                assert "${" not in rendered, f"{path}: {rendered!r} left a placeholder"


def test_with_a_wiki_the_rendered_claude_md_says_how_to_navigate_with_it(tmp_path: Path, instance):
    wiki = tmp_path / "data" / "wiki"

    text = (_render(instance, wiki=wiki) / "CLAUDE.md").read_text()
    claude_md = " ".join(text.split())  # phrases may wrap across lines

    assert f"`{wiki}`" in claude_md
    # Start from the index and the pages, widen with aliases and addresses, answer from episodes.
    for value in ("`index.md`", "aliases", "addresses", "open the episodes"):
        assert value in claude_md, value
    # Cite episodes only; the wiki is untrusted, possibly wrong, and never exhaustive.
    for value in ("never a page", "untrusted mail", "wrong", "never exhaustive"):
        assert value in claude_md, value
    # A correction is restated in one sentence and takes effect after the next librarian run.
    for value in ("one sentence", "next librarian run"):
        assert value in claude_md, value
    # It points to the schema shared with the librarian.
    assert "`wiki-schema.md`" in claude_md
    assert "${" not in text, "a placeholder was left unfilled"


def test_with_a_wiki_the_settings_attach_it_after_l0_and_scratch(tmp_path: Path, instance):
    wiki = tmp_path / "data" / "wiki"
    target = _render(instance, wiki=wiki)

    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"] == [
        str(instance["l0"]),
        str(instance["scratch"]),
        str(wiki),
    ]


def test_with_a_wiki_the_project_holds_the_schema_shared_with_the_librarian(
    tmp_path: Path, instance
):
    target = _render(instance, wiki=tmp_path / "data" / "wiki")

    assert (target / "wiki-schema.md").read_text() == WIKI_SCHEMA.read_text()
    assert not (target / WIKI_SECTION).exists()


def test_without_a_wiki_the_project_says_nothing_of_one(instance):
    target = _render(instance)

    claude_md = (target / "CLAUDE.md").read_text()
    for value in ("index.md", "wiki-schema.md", "librarian"):
        assert value not in claude_md, value
    assert not (target / "wiki-schema.md").exists()
    settings = json.loads((target / SETTINGS).read_text())
    assert len(settings["permissions"]["additionalDirectories"]) == 2


def test_the_source_project_names_no_owner_and_no_instance_path():
    for path in _files(AGENT_PROJECT):
        text = (AGENT_PROJECT / path).read_text()
        for value in ("Argus", "McNevans", "@example."):
            assert value not in text, f"{path} names {value!r}"
    settings = json.loads((AGENT_PROJECT / SETTINGS).read_text())
    for value in _strings(settings):
        assert not value.startswith("/"), f"settings.json names an absolute path: {value!r}"


def test_the_permission_policy_denies_the_web_and_mcp_and_holds_in_every_mode(instance):
    """The shared policy. The web tools and MCP are denied here and again by the VM's managed
    settings (#92), so the policy holds even if these settings change. Isolation is the VM's: its
    managed settings, sandbox and network. What the settings fence is a deny rule, never an ask
    rule, since the chat client picks the permission mode."""
    settings = json.loads((_render(instance) / SETTINGS).read_text())
    permissions = settings["permissions"]

    assert "ask" not in permissions
    assert "defaultMode" not in permissions
    assert "allow" not in permissions
    assert {"WebSearch", "WebFetch", "mcp__*"} <= set(permissions["deny"])
    # The assistant's own tools aren't denied.
    assert not {"Bash", "Read", "Edit", "Write"} & set(permissions["deny"])
    # No MCP servers: none configured, claude.ai connectors off, and every MCP tool denied.
    assert settings["disableClaudeAiConnectors"] is True
    assert settings["enableAllProjectMcpServers"] is False
    assert "mcpServers" not in settings


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


@pytest.mark.parametrize(
    "path", ['/data/"quoted"/wiki', "/data/back\\slash/wiki", "/data/dollar$sign/wiki"]
)
def test_wiki_paths_with_json_or_template_characters_render_intact(tmp_path: Path, path: str):
    target = tmp_path / "rendered"

    render_agent_project(
        target, owner=OWNER, l0=tmp_path / "l0", scratch=tmp_path / "scratch", wiki=Path(path)
    )

    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"][2] == path
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


def test_the_render_command_attaches_a_given_wiki(instance, owner_env, tmp_path: Path):
    wiki = tmp_path / "data" / "wiki"

    assert main([*_render_command(instance), "--wiki", str(wiki)]) == 0

    target = instance["target"]
    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"][2] == str(wiki.resolve())
    assert f"`{wiki.resolve()}`" in (target / "CLAUDE.md").read_text()


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
