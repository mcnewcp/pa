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
    "gmail_stages": "GMAIL_STAGES",
    "icloud_stages": "ICLOUD_STAGES",
    "backup_stages": "BACKUP_STAGES",
    "schedule_stages": "SCHEDULE_STAGES",
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


def test_the_vm_is_built_open_then_fenced_before_the_store_or_a_credential_goes_in():
    """A rebuild never exposes the store or the login to an open network (#91)."""
    body = text()

    def at(snippet: str) -> int:
        assert body.count(snippet) == 1, snippet
        return body.index(snippet)

    create = at('run "$DEPLOY_DIR/create-vm.sh"\n')
    fence = at('run "$DEPLOY_DIR/fence-vm.sh"\n')
    mount = at('run "$DEPLOY_DIR/mount-store.sh"\n')
    login = at('-c "$PA_VM_CLAUDE auth login"')
    assert create < fence < mount < login
    assert "tailscale up" not in body


def test_the_wizard_parses():
    result = subprocess.run(["bash", "-n", str(WIZARD)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_an_unknown_option_is_refused_with_the_usage():
    result = subprocess.run(
        [str(WIZARD), "--everything"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )

    assert result.returncode == 2
    assert (
        "usage: setup-wizard.sh "
        "[--librarian | --vault | --gmail | --icloud | --backup | --schedules]" in result.stderr
    )


def test_the_schedules_are_turned_on_by_their_installer_after_the_backup_drive():
    """Last: the nightly job backs up to the drive, and the wiki must be seeded first."""
    body = text()

    backup = body.index('run "$DEPLOY_DIR/install-backup-drive.sh"')
    schedules = body.index('run "$DEPLOY_DIR/install-schedules.sh" --enable')
    assert backup < schedules
