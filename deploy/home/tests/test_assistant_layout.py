"""assistant-layout.sh, which install-assistant-fences.sh runs in the VM as root: the assistant's
writable directories outside its home, its user settings held empty by root, and no auto memory
left from earlier sessions (#92). It runs here on a temporary home with a stand-in `chown`, which
records what it was asked to do, since only root can give a file away."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
LAYOUT = DEPLOY / "assistant-layout.sh"

STAND_IN_CHOWN = """\
import json, os, sys
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def lay_out(tmp_path: Path, home: Path) -> list[list[str]]:
    """Runs the script as install-assistant-fences.sh does, and returns the chown calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    chown = bin_dir / "chown"
    chown.write_text(f"#!{sys.executable}\n{STAND_IN_CHOWN}")
    chown.chmod(chown.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "chown-calls"
    args = ["assistant", home, tmp_path / "srv" / "scratch", tmp_path / "srv" / "eval"]
    completed = subprocess.run(
        ["bash", "-c", 'umask 022; exec bash -s -- "$@"', "layout", *map(str, args)],
        stdin=LAYOUT.open(),
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "CALLS": str(calls)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [json.loads(line) for line in calls.read_text().splitlines()]


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_scratch_and_the_eval_directory_are_the_assistants_alone(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()

    chowned = lay_out(tmp_path, home)

    for name in ("scratch", "eval"):
        path = tmp_path / "srv" / name
        assert path.is_dir()
        assert mode(path) == 0o700
        assert ["assistant:assistant", str(path)] in chowned


def test_the_user_settings_are_an_empty_object_root_holds(tmp_path: Path):
    """No setting turns user settings off, so a root-owned {} stands in front of them."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude" / "settings.json"
    settings.write_text('{"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}\n')

    chowned = lay_out(tmp_path, home)

    assert json.loads(settings.read_text()) == {}
    assert mode(settings) == 0o644
    # Given to root under a new name, before it's renamed into place.
    [(_root, given)] = [call for call in chowned if call[0] == "root:root"]
    assert Path(given).parent == settings.parent


def test_a_link_in_place_of_the_user_settings_is_replaced_not_written_through(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("left alone\n")
    settings = home / ".claude" / "settings.json"
    settings.symlink_to(elsewhere)

    lay_out(tmp_path, home)

    assert not settings.is_symlink()
    assert json.loads(settings.read_text()) == {}
    assert elsewhere.read_text() == "left alone\n"


def test_a_home_without_claude_code_state_yet_gets_its_directory(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()

    chowned = lay_out(tmp_path, home)

    assert (home / ".claude").is_dir()
    assert ["assistant:assistant", str(home / ".claude")] in chowned
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {}


def test_every_auto_memory_directory_is_removed_and_transcripts_are_kept(tmp_path: Path):
    home = tmp_path / "home"
    projects = home / ".claude" / "projects"
    for project in ("-home-assistant-agent", "-srv-pa-agent"):
        (projects / project / "memory").mkdir(parents=True)
        (projects / project / "memory" / "MEMORY.md").write_text("- a note\n")
        (projects / project / "3f0c9a7e.jsonl").write_text("{}\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (projects / "-tmp-x").mkdir()
    (projects / "-tmp-x" / "memory").symlink_to(elsewhere)

    lay_out(tmp_path, home)

    assert sorted(p.relative_to(projects).as_posix() for p in projects.rglob("*")) == [
        "-home-assistant-agent",
        "-home-assistant-agent/3f0c9a7e.jsonl",
        "-srv-pa-agent",
        "-srv-pa-agent/3f0c9a7e.jsonl",
        "-tmp-x",
    ]
    assert elsewhere.is_dir()
