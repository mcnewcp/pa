"""Development sessions in this repository can't read the real data root.

The repository is public, and a development session holds a shell, credentials, web access, and
push rights, so it must never read real content. Its Claude Code settings deny the real data
root to the file tools and to the shell's file commands; `pa inspect` is how it sees a payload,
and a diagnosis is how it asks about what one says (ADR-0010). Neither changes a rule.
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


VAULT_COPY = "~/.local/share/pa-vault"
SYNC_CLIENT_STATE = "~/.config/obsidian-headless"


def test_the_vault_copy_and_the_sync_clients_state_are_denied_too():
    """The devbox's copy of the owner's vault holds their notes, and the headless Sync client's
    state holds its login, the vault's key, and a log naming every note."""
    for path in (VAULT_COPY, SYNC_CLIENT_STATE):
        for tool in ("Read", "Edit"):
            assert f"{tool}({path})" in denied()
            assert f"{tool}({path}/**)" in denied()
        for command in ("grep", "rg"):
            bare = path.removeprefix("~/")
            assert f"Bash({command} *{bare})" in denied()
            assert f"Bash({command} *{bare} *)" in denied()
            assert f"Bash({command} *{bare}/*)" in denied()


def rules_for(path: str) -> list[str]:
    """The ten rules that deny one directory: to the file tools, and to the shell's searches."""
    bare = path.removeprefix("~/")
    return [
        *(f"{tool}({path}{under})" for tool in ("Read", "Edit") for under in ("", "/**")),
        *(
            f"Bash({command} *{bare}{rest})"
            for command in ("grep", "rg")
            for rest in ("", " *", "/*")
        ),
    ]


def test_the_diagnostician_changes_no_rule():
    """A question about real content goes to the diagnostician (ADR-0010), which adds a way to
    ask and removes no fence: the rules are the ones there were before it, nothing is allowed
    past them, and the settings hold nothing else."""
    settings = json.loads((REPOSITORY / ".claude" / "settings.json").read_text())

    assert settings == {
        "permissions": {
            "deny": [
                *rules_for(REAL_DATA_ROOT),
                *rules_for(VAULT_COPY),
                *rules_for(SYNC_CLIENT_STATE),
            ]
        }
    }


def test_the_released_directory_stays_readable():
    """What the release check let through is for a development session to read: it is outside
    the real data root, and no rule denies it."""
    assert not [rule for rule in denied() if "pa-released" in rule]
