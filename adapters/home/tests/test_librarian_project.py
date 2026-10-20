"""Rendering the librarian project: a filled-in copy of `librarian/` for one instance.

The source project is shared by both instances and names none of them; a rendered copy carries
the owner and the paths the session sees L0 and the wiki at, and nothing else differs.
"""

import json
import re
from datetime import date
from pathlib import Path, PurePosixPath

import pytest

from pa_core.librarian.lint import L0Metadata, Thresholds, lint
from pa_core.librarian.wiki import STATUSES, parse_decision, parse_loop, parse_observation
from pa_core.owner import Owner
from pa_home.claude_session import FILE_TOOLS
from pa_home.cli import main
from pa_home.librarian_project import LIBRARIAN_PROJECT, WIKI_SCHEMA, render_librarian_project
from pa_home.project import SETTINGS, ProjectRenderError

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
    for value in ("Argus McNevans", "Also called: Gus", "`argus@example.com`", str(L0), str(WIKI)):
        assert value in claude_md
    assert "${" not in claude_md, "a placeholder was left unfilled"
    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"] == [str(L0), str(WIKI)]


def test_the_settings_name_only_the_file_tools_and_no_mcp(tmp_path: Path):
    """The settings allow the file tools the session is started with (`--tools`) and nothing
    else; the VM's managed settings deny every other tool."""
    settings = json.loads((_render(tmp_path) / SETTINGS).read_text())

    assert set(settings["permissions"]) == {"allow", "additionalDirectories"}
    assert sorted(settings["permissions"]["allow"]) == sorted(FILE_TOOLS)
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


def test_the_instructions_point_at_the_wiki_schema_and_every_template():
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()

    assert WIKI_SCHEMA.is_file() and WIKI_SCHEMA.parent == LIBRARIAN_PROJECT
    assert f"`{WIKI_SCHEMA.name}`" in claude_md
    for template in (LIBRARIAN_PROJECT / "templates").iterdir():
        assert f"`templates/{template.name}`" in claude_md


def test_the_wiki_schema_is_written_for_any_reader_and_names_no_instance_path():
    """The assistant reads the same schema (#67), and sees the wiki at another path, so the
    schema gives paths relative to the wiki and holds no placeholders."""
    schema = WIKI_SCHEMA.read_text()

    assert "${" not in schema
    for folder in ("days/", "people/", "orgs/", "topics/", "index.md"):
        assert folder in schema


def test_the_wiki_schemas_example_lines_are_well_formed():
    """Its examples are what readers copy, so each must parse as the line it shows."""
    blocks = re.findall(r"^```\n(.*?)^```$", WIKI_SCHEMA.read_text(), re.MULTILINE | re.DOTALL)
    examples = [
        line
        for block in blocks
        for line in block.splitlines()
        if line.startswith("- ") and "<" not in line
    ]

    assert len(examples) >= 4
    for line in examples:
        parsed = parse_observation(line) or parse_decision(line) or parse_loop(line)
        assert parsed is not None, line


def test_rendering_refuses_a_target_that_already_has_files(tmp_path: Path):
    target = tmp_path / "rendered"
    target.mkdir()
    (target / "notes.txt").write_text("keep me")

    with pytest.raises(ProjectRenderError, match="not empty"):
        render_librarian_project(target, owner=OWNER, l0=L0, wiki=WIKI)

    assert _files(target) == [Path("notes.txt")]


@pytest.fixture
def owner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PA_DATA_DIR", raising=False)
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", "Gus")


def test_the_render_command_fills_in_the_owner_and_the_paths_the_session_sees(
    tmp_path: Path, owner_env, capsys: pytest.CaptureFixture[str]
):
    target = tmp_path / "rendered"

    # The paths are where the session sees L0 and the wiki (in its VM): they needn't exist here.
    assert (
        main(["librarian-project", "render", str(target), "--l0", str(L0), "--wiki", str(WIKI)])
        == 0
    )

    claude_md = (target / "CLAUDE.md").read_text()
    assert "Argus McNevans" in claude_md and "`argus@example.com`" in claude_md
    settings = json.loads((target / SETTINGS).read_text())
    assert settings["permissions"]["additionalDirectories"] == [str(L0), str(WIKI)]
    assert (target / "wiki-schema.md").read_text() == WIKI_SCHEMA.read_text()
    assert str(target.resolve()) in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (["--l0", "l0", "--wiki", str(WIKI)], "absolute"),
        (["--l0", str(L0), "--wiki", "wiki"], "absolute"),
    ],
)
def test_the_render_command_needs_absolute_session_paths(
    tmp_path: Path, owner_env, capsys: pytest.CaptureFixture[str], args: list[str], reason: str
):
    target = tmp_path / "rendered"

    assert main(["librarian-project", "render", str(target), *args]) != 0

    assert reason in capsys.readouterr().err
    assert not target.exists()


def test_the_render_command_refuses_a_target_inside_the_repository(
    owner_env, capsys: pytest.CaptureFixture[str]
):
    target = LIBRARIAN_PROJECT.parent / "rendered-librarian"

    assert (
        main(["librarian-project", "render", str(target), "--l0", str(L0), "--wiki", str(WIKI)])
        != 0
    )

    assert "inside the repository" in capsys.readouterr().err
    assert not target.exists()


# The templates: a page or day log with a `<placeholder>` wherever the librarian writes. Filled
# in, each passes lint; left as is, each fails it, so a leftover placeholder never lands.

TEMPLATES = LIBRARIAN_PROJECT / "templates"
EPISODE = "gmail_0123456789abcdef"
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
FILLS = {
    # Frontmatter and titles.
    "<other names they go by>": "Dave",
    "<their addresses, from the identity table>": "dave@example.com, david.kim@example.org",
    "<one line: who they are to me>": "Neighbor; borrowed the van in October.",
    "<their name>": "David Kim",
    "<other names it goes by>": "the clinic",
    "<its mail domains, from the identity table>": "riverside-clinic.example",
    "<one line: what it is to me>": "My doctor's office.",
    "<its name>": "Riverside Clinic",
    "<one line: where it stands>": "Kitchen remodel; waiting on the contractor's quote.",
    "<what I call it>": "Kitchen remodel",
    "<YYYY-MM-DD>": "2026-10-05",
    # Lines.
    "<episode_id>": EPISODE,
    "<where things stand, citing the episodes that show it>": "The quote is due Friday.",
    "<anything else worth keeping, citing its episodes>": "Prefers texts to calls.",
    "<subject>": "Countertops",
    "<choice>": "quartz",
    "<who>": "me, [[david-kim]]",
    "<owed by>": "[[david-kim]]",
    "<owed to>": "me",
    "<what is owed>": "return the van keys",
    "<what the episode shows, in one line, linking at least one [[slug]]>": (
        "[[david-kim]] asked to borrow the van"
    ),
}
WIKI_PATHS = {
    "person.md": "people/david-kim.md",
    "organization.md": "orgs/riverside-clinic.md",
    "topic.md": "topics/kitchen-remodel.md",
    "day.md": "days/2026-10-05.md",
}


def _fill(placeholder: re.Match[str]) -> str:
    """A choice like `<asked|promised>` takes its first option; any other placeholder must be
    one this test knows."""
    text = placeholder[0]
    if "|" in text:
        return text[1:-1].split("|")[0]
    assert text in FILLS, f"a template placeholder this test doesn't fill: {text}"
    return FILLS[text]


def _lint_one(tmp_path: Path, wiki_path: str, text: str) -> list[str]:
    tree = tmp_path / "wiki"
    (tree / wiki_path).parent.mkdir(parents=True)
    (tree / wiki_path).write_text(text)
    l0 = L0Metadata(
        episode_ids=frozenset({EPISODE}),
        addresses=frozenset(
            {"dave@example.com", "david.kim@example.org", "front-desk@riverside-clinic.example"}
        ),
    )
    thresholds = Thresholds(today=date(2026, 10, 5), page_threshold=3, loop_age_days=30)
    return [str(finding) for finding in lint(tree, l0=l0, thresholds=thresholds).errors]


def test_there_are_templates_for_each_page_type_and_the_day_log():
    assert sorted(p.name for p in TEMPLATES.iterdir()) == sorted(WIKI_PATHS)


@pytest.mark.parametrize("template", sorted(WIKI_PATHS))
def test_a_filled_in_template_passes_lint(tmp_path: Path, template: str):
    filled = PLACEHOLDER.sub(_fill, (TEMPLATES / template).read_text())

    assert _lint_one(tmp_path, WIKI_PATHS[template], filled) == []


@pytest.mark.parametrize("template", sorted(WIKI_PATHS))
def test_a_template_left_unfilled_fails_lint(tmp_path: Path, template: str):
    assert _lint_one(tmp_path, WIKI_PATHS[template], (TEMPLATES / template).read_text())


@pytest.mark.parametrize("template", ["person.md", "organization.md", "topic.md"])
def test_each_page_template_lists_its_types_allowed_statuses(template: str):
    page_type = template.removesuffix(".md")
    [status] = [
        line
        for line in (TEMPLATES / template).read_text().splitlines()
        if line.startswith("status: ")
    ]

    assert status == f"status: <{'|'.join(STATUSES[page_type])}>"
