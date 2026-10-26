"""`pa capture` and `pa status` over a temporary data root and vault, configured as the owner
would configure them."""

import logging
import re
from pathlib import Path

import pytest

from pa_home.cli import main
from pa_home.config import DataRoot
from pa_home.lock import data_root_lock

# Distinctive text that must never reach the output, the logs, or the status file.
SALT = "Quillon Vasquez-Ferro booked the Bellwether loft"


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    return root


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vault"
    (path / "Journal" / "Daily").mkdir(parents=True)
    monkeypatch.setenv("PA_VAULT_DIR", str(path))
    monkeypatch.setenv("PA_VAULT_DAILY_NOTES", "Journal/Daily")
    return path


def write_note(vault: Path, day: str, text: str) -> None:
    (vault / "Journal" / "Daily" / f"{day}.md").write_text(text)


def episodes(data_root: Path) -> list[Path]:
    return sorted((data_root / "l0" / "episodes").rglob("*.json"))


def test_pa_capture_lands_finished_notes_and_reports_counts_without_content(
    data_root: Path,
    vault: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    write_note(vault, "2020-01-01", f"## Plans\n{SALT}.\n")
    write_note(vault, "2020-01-02", "## Errands\nPicked up the bikes.\n")

    assert main(["capture"]) == 0

    output = capsys.readouterr()
    assert len(episodes(data_root)) == 2
    assert "obsidian: collected 2" in output.out
    assert "ingested 2, unchanged 0, quarantined 0" in output.out
    assert SALT not in output.out + output.err + caplog.text


def test_pa_status_prints_watermarks_cadences_and_failures_without_content(
    data_root: Path,
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PA_OBSIDIAN_CADENCE_MINUTES", "60")
    write_note(vault, "2020-01-01", f"## Plans\n{SALT}.\n")
    main(["capture"])
    # The next collection fails: the daily-notes folder has gone.
    (vault / "Journal" / "Daily").rename(vault / "Journal" / "Old")
    assert main(["capture"]) == 1
    capsys.readouterr()

    assert main(["status"]) == 0

    output = capsys.readouterr()
    [line] = [line for line in output.out.splitlines() if line.startswith("obsidian:")]
    assert re.search(r"watermark \d{4}-\d{2}-\d{2} \d{2}:\d{2}", line)
    assert "every 60 min" in line
    assert "last failure" in line
    assert "the vault copy's daily-notes folder doesn't exist" in line
    assert "quarantined 0" in line
    status_file = DataRoot(data_root).status.read_text()
    for text in (output.out, output.err, caplog.text, status_file):
        assert SALT not in text


def test_pa_status_before_any_cycle_says_so(data_root: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["status"]) == 0

    assert capsys.readouterr().out == "status: nothing recorded yet\n"


def test_pa_capture_skips_while_another_run_holds_the_lock(
    data_root: Path, vault: Path, capsys: pytest.CaptureFixture[str]
):
    write_note(vault, "2020-01-01", "## Errands\nPicked up the bikes.\n")

    with data_root_lock(DataRoot(data_root)):
        assert main(["capture"]) == 0

    assert "skipped" in capsys.readouterr().out
    assert episodes(data_root) == []


def test_pa_capture_without_a_vault_configured_still_ingests(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["capture"]) == 0

    assert "ingested 0, unchanged 0, quarantined 0" in capsys.readouterr().out


def test_a_vault_without_its_daily_notes_folder_configured_is_refused(
    data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("PA_VAULT_DIR", str(tmp_path / "vault"))

    assert main(["capture"]) == 1

    assert "PA_VAULT_DAILY_NOTES" in capsys.readouterr().err
