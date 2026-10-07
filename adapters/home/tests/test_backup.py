import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pa_home.cli import main


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    for var in (
        "RESTIC_REPOSITORY",
        "RESTIC_REPOSITORY_FILE",
        "RESTIC_PASSWORD",
        "RESTIC_PASSWORD_FILE",
        "RESTIC_PASSWORD_COMMAND",
    ):
        monkeypatch.delenv(var, raising=False)
    return root


requires_restic = pytest.mark.skipif(shutil.which("restic") is None, reason="restic not installed")


@pytest.fixture
def restic_repository(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repository = data_root.parent / "restic-repo"
    monkeypatch.setenv("RESTIC_REPOSITORY", str(repository))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    restic("init")
    return repository


def restic(*args: str) -> str:
    return subprocess.run(["restic", *args], check=True, capture_output=True, text=True).stdout


def snapshots() -> list[dict[str, object]]:
    return json.loads(restic("snapshots", "--json"))


def files_under(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def populate(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An L0 with an episode and a quarantined payload, and a catalog beside it."""
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "hotel.eml").write_bytes(
        b"Message-ID: <wedding-0001@example.net>\r\n"
        b"Date: Wed, 30 Sep 2026 21:30:00 -0500\r\n"
        b"From: David Kim <dkim@example.net>\r\n"
        b"To: Argus McNevans <argus@example.com>\r\n"
        b"Subject: Hotel block\r\n"
        b"\r\n"
        b"The hotel block closes on Oct 9.\r\n"
    )
    (inbox / "garbled.txt").write_bytes(b"not a payload any source produces\n")
    assert main(["ingest"]) == 0


@requires_restic
def test_backup_creates_a_snapshot_in_the_configured_repository(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch
):
    populate(data_root, monkeypatch)

    assert main(["backup"]) == 0

    assert len(snapshots()) == 1


@requires_restic
def test_a_restored_snapshot_reproduces_l0_byte_for_byte_without_the_catalog(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    populate(data_root, monkeypatch)
    l0 = files_under(data_root / "l0")
    assert any(name.startswith("raw/") for name in l0)
    assert any(name.startswith("episodes/") for name in l0)
    assert any(name.startswith("quarantine/") for name in l0)
    assert (data_root / "catalog" / "catalog.sqlite").is_file()

    assert main(["backup"]) == 0
    restored = tmp_path / "restored"
    restic("restore", "latest", "--target", str(restored))

    assert files_under(restored) == l0
    assert not list(restored.rglob("catalog*"))


def test_backup_without_a_repository_fails_with_a_clear_message(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")

    assert main(["backup"]) == 1

    error = capsys.readouterr().err
    assert "Backup configuration is missing" in error
    assert "RESTIC_REPOSITORY" in error
    assert "RESTIC_PASSWORD" not in error


def test_backup_without_a_password_fails_with_a_clear_message(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("RESTIC_REPOSITORY", str(data_root.parent / "restic-repo"))

    assert main(["backup"]) == 1

    error = capsys.readouterr().err
    assert "Backup configuration is missing" in error
    assert "RESTIC_PASSWORD" in error
    assert "RESTIC_REPOSITORY" not in error


def test_backup_without_restic_installed_fails_with_a_clear_message(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RESTIC_REPOSITORY", str(tmp_path / "restic-repo"))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    (data_root / "l0").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    assert main(["backup"]) == 1

    assert "restic is not installed" in capsys.readouterr().err
