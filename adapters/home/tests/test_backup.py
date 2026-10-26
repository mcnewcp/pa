import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.status import JobStatus, read_status
from pa_home.cli import main
from pa_home.config import DataRoot
from pa_home.lock import data_root_lock


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
    (inbox / "gmail").mkdir(parents=True)
    (inbox / "gmail" / "hotel.eml").write_bytes(
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


def populate_wiki_and_evals(data_root: Path) -> None:
    """The wiki's working tree and git directory, and the private eval directory, beside L0."""
    git = data_root / "wiki.git"
    (git / "objects" / "ab").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "objects" / "ab" / "cdef").write_bytes(b"\x78\x01 a loose object")
    (data_root / "wiki" / "days").mkdir(parents=True)
    (data_root / "wiki" / "days" / "2026-09-30.md").write_text("checked out from wiki.git\n")
    evals = data_root / "evals"
    (evals / "sets").mkdir(parents=True)
    (evals / "sets" / "real.yaml").write_text("questions: []\n")


def restore_latest(target: Path) -> Path:
    restic("restore", "latest", "--target", str(target))
    return target


@requires_restic
def test_a_restore_reproduces_l0_the_wikis_git_directory_and_the_evals_and_nothing_else(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    populate(data_root, monkeypatch)
    populate_wiki_and_evals(data_root)
    l0 = files_under(data_root / "l0")
    assert any(name.startswith("raw/") for name in l0)
    assert any(name.startswith("episodes/") for name in l0)
    assert any(name.startswith("quarantine/") for name in l0)
    assert (data_root / "catalog" / "catalog.sqlite").is_file()

    assert main(["backup"]) == 0
    restored = restore_latest(tmp_path / "restored")

    assert len(snapshots()) == 1
    assert sorted(p.name for p in restored.iterdir()) == ["evals", "l0", "wiki.git"]
    assert files_under(restored / "l0") == l0
    assert files_under(restored / "wiki.git") == files_under(data_root / "wiki.git")
    assert files_under(restored / "evals") == files_under(data_root / "evals")


@requires_restic
def test_a_data_root_without_a_wiki_or_evals_yet_backs_up_l0(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    populate(data_root, monkeypatch)

    assert main(["backup"]) == 0
    restored = restore_latest(tmp_path / "restored")

    assert sorted(p.name for p in restored.iterdir()) == ["l0"]
    assert files_under(restored / "l0") == files_under(data_root / "l0")


@requires_restic
def test_a_tree_left_by_an_interrupted_renormalize_is_not_backed_up(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    populate(data_root, monkeypatch)
    l0 = files_under(data_root / "l0")
    leftover = data_root / "l0" / ".episodes.renormalize" / "gmail" / "2026-09" / "gmail_x.json"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("{}")

    assert main(["backup"]) == 0
    restored = restore_latest(tmp_path / "restored")

    assert files_under(restored / "l0") == l0


def test_backup_without_a_repository_fails_with_a_clear_message_and_is_recorded(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")

    assert main(["backup"]) == 1

    error = capsys.readouterr().err
    assert "Backup configuration is missing" in error
    assert "RESTIC_REPOSITORY" in error
    assert "RESTIC_PASSWORD" not in error
    failure = backup_status(data_root).last_failure
    assert failure is not None
    assert failure.reason.startswith("Backup configuration is missing: set RESTIC_REPOSITORY")


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


def backup_status(data_root: Path) -> JobStatus:
    return read_status(DataRoot(data_root).status).backup


@requires_restic
def test_a_backup_that_succeeds_is_recorded_in_the_status(
    data_root: Path, restic_repository: Path, monkeypatch: pytest.MonkeyPatch
):
    populate(data_root, monkeypatch)
    before = datetime.now(UTC)

    assert main(["backup"]) == 0

    status = backup_status(data_root)
    assert status.last_success is not None
    assert before <= status.last_success <= datetime.now(UTC)
    assert status.last_failure is None


@requires_restic
def test_a_missing_repository_is_recorded_as_a_failure_in_the_status(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    populate(data_root, monkeypatch)
    # The repository is on the backup drive, which isn't mounted.
    repository = data_root.parent / "pa-backup-drive" / "restic"
    monkeypatch.setenv("RESTIC_REPOSITORY", str(repository))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    before = datetime.now(UTC)

    assert main(["backup"]) == 1

    error = capsys.readouterr().err
    assert "The restic repository is missing: is the backup drive mounted?" in error
    assert str(repository) not in error  # restic's own output is never passed on
    status = backup_status(data_root)
    assert status.last_success is None
    assert status.last_failure is not None
    assert before <= status.last_failure.at <= datetime.now(UTC)
    assert status.last_failure.reason == (
        "The restic repository is missing: is the backup drive mounted?"
    )


@requires_restic
def test_a_later_success_keeps_the_last_failure(data_root: Path, monkeypatch: pytest.MonkeyPatch):
    populate(data_root, monkeypatch)
    repository = data_root.parent / "restic-repo"
    monkeypatch.setenv("RESTIC_REPOSITORY", str(repository))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    assert main(["backup"]) == 1
    failure = backup_status(data_root).last_failure
    restic("init")

    assert main(["backup"]) == 0

    status = backup_status(data_root)
    assert status.last_failure == failure
    assert failure is not None and status.last_success is not None
    assert status.last_success > failure.at


@requires_restic
def test_pa_backup_skips_while_another_run_holds_the_lock(
    data_root: Path,
    restic_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    populate(data_root, monkeypatch)
    capsys.readouterr()

    with data_root_lock(DataRoot(data_root)):
        assert main(["backup"]) == 0

    assert capsys.readouterr().out == "backup: skipped: another run holds the data root's lock\n"
    assert snapshots() == []
    assert backup_status(data_root) == JobStatus()


def test_a_backup_that_fails_unexpectedly_is_recorded_without_its_message(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("RESTIC_REPOSITORY", str(tmp_path / "restic-repo"))
    monkeypatch.setenv("RESTIC_PASSWORD", "correct horse")
    (data_root / "l0").mkdir(parents=True)
    bin_dir = tmp_path / "broken-bin"
    bin_dir.mkdir()
    (bin_dir / "restic").write_text("#!/nonexistent/interpreter\n")
    (bin_dir / "restic").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    with pytest.raises(FileNotFoundError):
        main(["backup"])

    failure = backup_status(data_root).last_failure
    assert failure is not None
    assert failure.reason == "FileNotFoundError: No such file or directory"
