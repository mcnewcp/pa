"""The setup wizard's shape: each group of stages counts the stages it shows, and each choice of
groups adds up, so the progress line never reads "Stage 9/8". The stages themselves are walked by
the owner on the devbox."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

WIZARD = Path(__file__).resolve().parents[1] / "setup-wizard.sh"

# Each group of stages: its function in the wizard, and the variable that counts its stages.
GROUPS = {
    "assistant_stages": "ASSISTANT_STAGES",
    "librarian_stages": "LIBRARIAN_STAGES",
    "vault_stages": "VAULT_STAGES",
}


def text() -> str:
    return WIZARD.read_text()


def stages_in(function: str) -> int:
    body = re.search(rf"^{function}\(\) {{\n(.*?)^}}", text(), re.MULTILINE | re.DOTALL)
    assert body, f"no {function} in the wizard"
    return len(re.findall(r'^stage "', body.group(1), re.MULTILINE))


def declared(variable: str) -> int:
    value = re.search(rf"^{variable}=(\d+)$", text(), re.MULTILINE)
    assert value, f"no {variable} in the wizard"
    return int(value.group(1))


@pytest.mark.parametrize(("function", "variable"), GROUPS.items())
def test_each_group_counts_its_stages(function: str, variable: str):
    assert declared(variable) == stages_in(function)


def test_the_wizard_parses():
    result = subprocess.run(["bash", "-n", str(WIZARD)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_an_unknown_option_is_refused_with_the_usage():
    result = subprocess.run(
        [str(WIZARD), "--everything"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )

    assert result.returncode == 2
    assert "usage: setup-wizard.sh [--librarian | --vault]" in result.stderr
