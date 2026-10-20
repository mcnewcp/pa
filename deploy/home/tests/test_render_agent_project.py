"""render-agent-project.sh, which update.sh runs in the VM as the assistant: the agent project is
rendered with the wiki only when there is one (story 101). It runs here on a stand-in `pa` that
records its arguments."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
RENDER = DEPLOY / "render-agent-project.sh"


def render(tmp_path: Path, wiki: Path) -> list[str]:
    """Runs the script as update.sh does (on stdin, to `bash -s`) and returns the arguments the
    stand-in `pa` was given."""
    repo = tmp_path / "pa"
    pa = repo / ".venv" / "bin" / "pa"
    pa.parent.mkdir(parents=True)
    pa.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >"$(dirname "$0")/args"\n')
    pa.chmod(pa.stat().st_mode | stat.S_IXUSR)
    owner_env = tmp_path / "owner.env"
    owner_env.write_text("PA_OWNER_NAME='Argus McNevans'\n")
    project, scratch = tmp_path / "agent", tmp_path / "scratch"
    project.mkdir()
    (project / "stale.md").write_text("from the last render\n")
    args = [repo, owner_env, project, "/srv/pa/l0", scratch, wiki]
    completed = subprocess.run(
        ["bash", "-s", "--", *map(str, args)],
        stdin=RENDER.open(),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not project.exists(), "the last rendered project is deleted before the render"
    assert scratch.is_dir()
    return (pa.parent / "args").read_text().splitlines()


def test_with_a_wiki_the_agent_project_is_rendered_with_it(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# Index\n\nNo pages yet.\n")

    args = render(tmp_path, wiki)

    assert args[-2:] == ["--wiki", str(wiki)]


def test_without_a_wiki_the_agent_project_is_rendered_for_files_alone(tmp_path: Path):
    assert "--wiki" not in render(tmp_path, tmp_path / "not-mounted")


def test_a_wiki_share_the_librarian_hasnt_run_into_yet_isnt_a_wiki(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    assert "--wiki" not in render(tmp_path, wiki)
