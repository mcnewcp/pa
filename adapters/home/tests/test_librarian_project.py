"""Rendering the librarian project: a filled-in copy of `librarian/` for one instance.

The source project is shared by both instances and names none of them; a rendered copy carries
the owner and the paths the session sees L0 and the wiki at, and nothing else differs.
"""

import json
from pathlib import Path, PurePosixPath

import pytest

from pa_core.owner import Owner
from pa_home.librarian_project import LIBRARIAN_PROJECT, SETTINGS, render_librarian_project
from pa_home.project import ProjectRenderError

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
L0 = PurePosixPath("/srv/pa/l0")
WIKI = PurePosixPath("/srv/pa/wiki")


def _files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def _render(tmp_path: Path) -> Path:
    target = tmp_path / "rendered"
    render_librarian_project(target, owner=OWNER, l0=L0, wiki=WIKI)
    return target


def test_the_rendered_project_names_the_owner_and_where_l0_and_the_wiki_are(tmp_path: Path):
    target = _render(tmp_path)

    claude_md = (target / "CLAUDE.md").read_text()
    for value in ("Argus McNevans", "`argus@example.com`", str(L0), str(WIKI)):
        assert value in claude_md
    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"] == [str(L0), str(WIKI)]


def test_the_settings_allow_no_tools_and_no_mcp(tmp_path: Path):
    """The tools come from `--tools` and the VM's managed settings, never from the project."""
    settings = json.loads((_render(tmp_path) / SETTINGS).read_text())

    assert set(settings["permissions"]) == {"additionalDirectories"}
    assert "hooks" not in settings
    assert "mcpServers" not in settings
    assert settings["enableAllProjectMcpServers"] is False
    assert settings["disableClaudeAiConnectors"] is True


def test_a_rendered_project_differs_from_the_source_only_where_instance_values_go(
    tmp_path: Path,
):
    target = _render(tmp_path)

    assert _files(target) == _files(LIBRARIAN_PROJECT)
    for path in _files(LIBRARIAN_PROJECT):
        source_lines = (LIBRARIAN_PROJECT / path).read_text().splitlines()
        rendered_lines = (target / path).read_text().splitlines()
        assert len(rendered_lines) == len(source_lines), path
        for source, rendered in zip(source_lines, rendered_lines, strict=True):
            if source != rendered:
                assert "${" in source, f"{path}: {source!r} changed with no placeholder"
                assert "${" not in rendered, f"{path}: {rendered!r} left a placeholder"


def test_the_source_project_names_no_owner_and_no_instance_path():
    for path in _files(LIBRARIAN_PROJECT):
        text = (LIBRARIAN_PROJECT / path).read_text()
        for value in ("Argus", "McNevans", "@example.", "/srv/", "/home/"):
            assert value not in text, f"{path} names {value!r}"


def test_rendering_refuses_a_target_that_already_has_files(tmp_path: Path):
    target = tmp_path / "rendered"
    target.mkdir()
    (target / "notes.txt").write_text("keep me")

    with pytest.raises(ProjectRenderError, match="not empty"):
        render_librarian_project(target, owner=OWNER, l0=L0, wiki=WIKI)

    assert _files(target) == [Path("notes.txt")]
