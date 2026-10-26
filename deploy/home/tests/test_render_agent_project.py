"""render-agent-project.sh, which update.sh runs in the VM as root: the agent project is rendered
with the wiki only when there is one (story 101), and it's left read-only to everyone but root
(#92). It runs here on a stand-in `pa` that records its arguments and writes a project."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
RENDER = DEPLOY / "render-agent-project.sh"

STAND_IN_PA = """\
#!/bin/bash
printf "%s\\n" "$@" >"$(dirname "$0")/args"
mkdir -p "$3/.claude/hooks"
echo "# Personal assistant" >"$3/CLAUDE.md"
echo "{}" >"$3/.claude/settings.json"
echo "import sys" >"$3/.claude/hooks/capture_exchange.py"
"""


def render(tmp_path: Path, wiki: Path, *, umask: str = "022") -> list[str]:
    """Runs the script as update.sh does (on stdin, to `bash -s`) and returns the arguments the
    stand-in `pa` was given."""
    repo = tmp_path / "pa"
    pa = repo / ".venv" / "bin" / "pa"
    pa.parent.mkdir(parents=True)
    pa.write_text(STAND_IN_PA)
    pa.chmod(pa.stat().st_mode | stat.S_IXUSR)
    owner_env = tmp_path / "owner.env"
    owner_env.write_text("PA_OWNER_NAME='Argus McNevans'\n")
    project, scratch = tmp_path / "agent", tmp_path / "scratch"
    project.mkdir()
    (project / "stale.md").write_text("from the last render\n")
    args = [repo, owner_env, project, "/srv/pa/l0", scratch, wiki]
    completed = subprocess.run(
        ["bash", "-c", f'umask {umask}; exec bash -s -- "$@"', "render", *map(str, args)],
        stdin=RENDER.open(),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (project / "stale.md").exists(), "the last rendered project is deleted first"
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


def test_the_rendered_project_is_readable_by_all_and_writable_by_its_owner_alone(tmp_path: Path):
    """Root renders it, so the assistant reads it, its hook included, and can't change it."""
    render(tmp_path, tmp_path / "not-mounted", umask="077")

    project = tmp_path / "agent"
    paths = [project, *project.rglob("*")]
    assert len(paths) == 6
    for path in paths:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o755 if path.is_dir() else 0o644), (path, oct(mode))
