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
    "owner_stages": "OWNER_STAGES",
    "assistant_stages": "ASSISTANT_STAGES",
    "librarian_stages": "LIBRARIAN_STAGES",
    "vault_stages": "VAULT_STAGES",
    "gmail_stages": "GMAIL_STAGES",
    "icloud_stages": "ICLOUD_STAGES",
    "backup_stages": "BACKUP_STAGES",
    "schedule_stages": "SCHEDULE_STAGES",
    "go_live_stages": "GO_LIVE_STAGES",
}


def text() -> str:
    return WIZARD.read_text()


def body_of(function: str) -> str:
    body = re.search(rf"^{function}\(\) {{\n(.*?)^}}", text(), re.MULTILINE | re.DOTALL)
    assert body, f"no {function} in the wizard"
    return body.group(1)


def stages_in(function: str) -> int:
    return len(re.findall(r'^stage "', body_of(function), re.MULTILINE))


def declared(variable: str) -> int:
    value = re.search(rf"^{variable}=(\d+)$", text(), re.MULTILINE)
    assert value, f"no {variable} in the wizard"
    return int(value.group(1))


@pytest.mark.parametrize(("function", "variable"), GROUPS.items())
def test_each_group_counts_its_stages(function: str, variable: str):
    assert declared(variable) == stages_in(function)


def test_the_vm_is_built_open_then_fenced_before_the_store_or_a_credential_goes_in():
    """A rebuild never exposes the store or the login to an open network (#91)."""
    body = body_of("assistant_stages")

    def at(snippet: str) -> int:
        assert body.count(snippet) == 1, snippet
        return body.index(snippet)

    create = at('run "$DEPLOY_DIR/create-vm.sh"\n')
    fence = at('run "$DEPLOY_DIR/fence-vm.sh"\n')
    mount = at('run "$DEPLOY_DIR/mount-store.sh"\n')
    login = at('-c "$PA_VM_CLAUDE auth login"')
    assert create < fence < mount < login
    assert "tailscale up" not in text()


def test_going_live_moves_the_vm_onto_the_real_data_root_and_checks_it_before_any_dry_run():
    """#99: the isolation and store checks pass before any collector runs against the real data
    root, and a check that fails stops the wizard (`run`) rather than being left for later."""
    body = body_of("go_live_stages")

    def at(snippet: str) -> int:
        assert body.count(snippet) == 1, snippet
        return body.index(snippet)

    steps = [
        at("sudo systemctl disable --now"),  # the timers, so no cycle runs in between
        at('release_data_root "$PA_SYNTHETIC_DATA_ROOT"'),
        at('run "$DEPLOY_DIR/install-chat-inbox.sh"'),
        at('run "$DEPLOY_DIR/install-wiki.sh"'),
        at('run "$DEPLOY_DIR/mount-store.sh"'),
        at("update_vm"),  # renders the agent project from the real owner.env
        at('run "$DEPLOY_DIR/install-schedules.sh"\n'),
        at('run "$DEPLOY_DIR/check-isolation.sh"'),
        at('run "$DEPLOY_DIR/check-store.sh"'),
        at('run "$DEPLOY_DIR/gmail.sh" dry-run'),
        at('run "$DEPLOY_DIR/calendar.sh" dry-run'),
    ]
    assert steps == sorted(steps)
    assert "--enable" not in body


def test_going_live_names_its_owner_first():
    block = re.search(r"^if \$only_go_live; then\n(.*?)^fi$", text(), re.M | re.S)
    assert block, "no go-live run in the wizard"

    assert re.findall(r"^  (\w+_stages)$", block.group(1), re.M) == [
        "owner_stages",
        "go_live_stages",
    ]
    assert re.search(
        r"^  --go-live\) TOTAL_STAGES=\$\(\(OWNER_STAGES \+ GO_LIVE_STAGES\)\);", text(), re.M
    )
    # It hands back to the session for the backfill, the replay and the seed, then the schedules.
    for step in (
        "capture.sh --backfill",
        "librarian.sh replay",
        "librarian.sh seed",
        "--schedules",
    ):
        assert step in block.group(1), step


def test_the_wizard_parses():
    result = subprocess.run(["bash", "-n", str(WIZARD)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_an_unknown_option_is_refused_with_the_usage():
    result = subprocess.run(
        [str(WIZARD), "--everything"], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )

    assert result.returncode == 2
    assert (
        "usage: setup-wizard.sh [--owner | --go-live | --librarian | --vault | --gmail | "
        "--icloud | --backup | --schedules]" in result.stderr
    )


def test_the_schedules_are_turned_on_by_their_installer_after_the_backup_drive():
    """Last: the nightly job backs up to the drive, and the wiki must be seeded first."""
    body = text()

    backup = body.index('run "$DEPLOY_DIR/install-backup-drive.sh"')
    schedules = body.index('run "$DEPLOY_DIR/install-schedules.sh" --enable')
    assert backup < schedules


def plain_run() -> list[str]:
    """The groups of stages a plain `setup-wizard.sh` runs, in order."""
    block = re.search(r"^elif \$only_librarian; then\n.*?^else\n(.*?)^fi$", text(), re.M | re.S)
    assert block, "no plain run in the wizard"
    return re.findall(r"^  (\w+_stages)$", block.group(1), re.M)


def test_a_plain_run_sets_up_gmail_and_icloud_after_the_data_root_and_before_the_schedules():
    groups = plain_run()

    assert groups == [
        "owner_stages",  # the data root and its owner, which the shares and the dry runs need
        "assistant_stages",
        "librarian_stages",
        "vault_stages",
        "gmail_stages",
        "icloud_stages",
        "backup_stages",
        "schedule_stages",
    ]


def test_a_plain_runs_progress_counts_every_stage_it_shows():
    total = re.search(r'^  ""\) TOTAL_STAGES=\$\(\((.*)\)\) ;;$', text(), re.M)
    assert total, "no total for the plain run"

    counted = sorted(variable.strip() for variable in total.group(1).split("+"))
    assert counted == sorted(GROUPS[group] for group in plain_run())


def test_the_owner_is_reminded_to_keep_remote_control_transcripts_out_of_training():
    """Story 129: with model-improvement sharing off, claude.ai keeps the transcripts 30 days."""
    body = text()

    assert 'stage "Keep your chats out of model training"' in body
    assert "Settings → Privacy" in body
