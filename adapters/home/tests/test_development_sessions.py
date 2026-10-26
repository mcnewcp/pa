"""Development sessions in this repository can't read the real data root.

The repository is public, and a development session holds a shell, credentials, web access, and
push rights, so it must never read real content. Its Claude Code settings deny the real data
root to the file tools and to the shell's file commands; `pa inspect` is how it sees a payload.
"""

from __future__ import annotations

import json
from pathlib import Path

REPOSITORY = Path(__file__).parents[3]
REAL_DATA_ROOT = "~/.local/share/pa-home"


def denied() -> list[str]:
    settings = json.loads((REPOSITORY / ".claude" / "settings.json").read_text())
    return settings["permissions"]["deny"]


def test_the_file_tools_are_denied_the_real_data_root_and_everything_in_it():
    for tool in ("Read", "Edit"):
        assert f"{tool}({REAL_DATA_ROOT})" in denied()
        assert f"{tool}({REAL_DATA_ROOT}/**)" in denied()


def test_searching_the_real_data_root_from_the_shell_is_denied():
    for command in ("grep", "rg"):
        for rule in (
            f"Bash({command} *.local/share/pa-home)",
            f"Bash({command} *.local/share/pa-home *)",
            f"Bash({command} *.local/share/pa-home/*)",
        ):
            assert rule in denied()


def test_the_synthetic_data_root_stays_readable():
    """Only the real data root is denied: development runs on `pa-home-synthetic` go on."""
    assert not [rule for rule in denied() if "pa-home-synthetic" in rule or "pa-home*" in rule]
