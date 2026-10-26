"""The backup drive and its restic repository on the devbox: the mount unit that finds the USB
SSD by its identity, the wizard's stage that mounts it and initializes the repository with a
password kept in the owner's config directory, and backup.sh, which runs `pa backup` against
it. They run here with HOME in a temporary directory and stand-ins for sudo, systemctl, lsblk,
mountpoint, restic and uv, which record how they were called, so nothing touches the host."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
UUID = "0f3c2a91-5b7e-4d1a-9c64-2e8b7a1d5f03"
MOUNT = "/mnt/pa-backup"
UNIT = r"mnt-pa\x2dbackup.mount"

RESTIC = shutil.which("restic")
requires_restic = pytest.mark.skipif(RESTIC is None, reason="restic not installed")
# The system's PATH, and restic's directory when it's installed elsewhere, such as ~/.local/bin.
SYSTEM_PATH = ":".join(([str(Path(RESTIC).parent)] if RESTIC else []) + ["/usr/bin", "/bin"])

# Each stand-in records its name, arguments, the restic variables it was given, and its stdin
# when it's sudo tee, then exits as the test's STAND_IN_EXITS says: {"name args...": code}, or
# {"name args...": [code, ...]} for the first call, the second, and so on (the last repeats).
STAND_IN = """\
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
key = " ".join([name, *args])
earlier = 0
if os.path.exists(os.environ["CALLS"]):
    with open(os.environ["CALLS"]) as recorded:
        earlier = sum(
            1 for line in recorded if " ".join([(c := json.loads(line))["name"], *c["args"]]) == key
        )
call = {"name": name, "args": args}
for var in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "RESTIC_PASSWORD", "PA_DATA_DIR"):
    if var in os.environ:
        call[var] = os.environ[var]
if name == "sudo" and args[:1] == ["tee"]:
    call["stdin"] = sys.stdin.read()
if name == "uv":
    call["args"] = args[args.index("--project") + 2:]
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(call) + "\\n")
if name == "lsblk":
    print(os.environ.get("ATTACHED_UUIDS", ""))
exits = json.loads(os.environ.get("STAND_IN_EXITS", "{}"))
code = exits.get(key, exits.get(name, 0))
if isinstance(code, list):
    code = code[min(earlier, len(code) - 1)]
sys.exit(code)
"""
STAND_INS = ("sudo", "systemctl", "lsblk", "mountpoint", "restic", "uv")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def run(
    tmp_path: Path,
    home: Path,
    command: list[str],
    *,
    stdin: str = "",
    exits: dict[str, int | list[int]] | None = None,
    attached: str = UUID,
    stand_ins: tuple[str, ...] = STAND_INS,
    environ: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in stand_ins:
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n{STAND_IN}")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:{SYSTEM_PATH}",
            "CALLS": str(tmp_path / "calls"),
            "STAND_IN_EXITS": json.dumps(exits or {}),
            "ATTACHED_UUIDS": attached,
            **(environ or {}),
        },
        check=False,
    )


def calls(tmp_path: Path, name: str | None = None) -> list[dict[str, Any]]:
    path = tmp_path / "calls"
    if not path.exists():
        return []
    recorded = [json.loads(line) for line in path.read_text().splitlines()]
    return [call for call in recorded if name is None or call["name"] == name]


def lib(tmp_path: Path, home: Path, script: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return run(
        tmp_path, home, ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'], **kwargs
    )


def password_file(home: Path) -> Path:
    return home / ".config" / "pa" / "restic.password"


# The mount unit


def test_the_drive_is_mounted_by_its_filesystems_identity(tmp_path: Path, home: Path):
    result = lib(tmp_path, home, f"backup_mount_unit {UUID}")

    assert result.returncode == 0, result.stderr
    unit = result.stdout
    assert f"What=/dev/disk/by-uuid/{UUID}\n" in unit
    assert f"Where={MOUNT}\n" in unit
    assert "Options=nosuid,nodev,noexec,noatime\n" in unit
    # Wanted, never required, at boot: a missing drive mustn't stop the devbox from booting.
    assert "WantedBy=multi-user.target\n" in unit
    assert "RequiredBy" not in unit


def test_the_units_name_is_its_mount_point_escaped(tmp_path: Path, home: Path):
    result = lib(tmp_path, home, "backup_mount_unit_name", stand_ins=())

    assert result.stdout.strip() == UNIT


@pytest.mark.parametrize("identity", ["/dev/sdb1", "sdb1", "", "0f3c 2a91", "../../sda"])
def test_anything_but_a_filesystem_uuid_is_refused(tmp_path: Path, home: Path, identity: str):
    result = lib(tmp_path, home, f"backup_mount_unit '{identity}'")

    assert result.returncode != 0
    assert "filesystem UUID" in result.stderr


def test_installing_the_drive_writes_and_starts_its_mount_unit_and_makes_the_repository_its_owners(
    tmp_path: Path, home: Path
):
    result = run(tmp_path, home, ["bash", str(DEPLOY / "install-backup-drive.sh"), UUID])

    assert result.returncode == 0, result.stderr
    sudo = calls(tmp_path, "sudo")
    tee = next(call for call in sudo if call["args"][0] == "tee")
    assert tee["args"][1] == f"/etc/systemd/system/{UNIT}"
    assert f"What=/dev/disk/by-uuid/{UUID}\n" in tee["stdin"]
    assert ["systemctl", "enable", "--now", UNIT] in [call["args"] for call in sudo]
    install = next(call for call in sudo if call["args"][0] == "install")
    assert install["args"][-1] == f"{MOUNT}/restic"
    assert "0700" in install["args"]


def test_a_drive_that_isnt_attached_is_refused(tmp_path: Path, home: Path):
    result = run(
        tmp_path, home, ["bash", str(DEPLOY / "install-backup-drive.sh"), UUID], attached=""
    )

    assert result.returncode != 0
    assert "plug in the backup drive" in result.stderr
    assert calls(tmp_path, "sudo") == []


def test_a_drive_that_doesnt_mount_leaves_no_repository_on_the_root_filesystem(
    tmp_path: Path, home: Path
):
    result = run(
        tmp_path,
        home,
        ["bash", str(DEPLOY / "install-backup-drive.sh"), UUID],
        exits={"mountpoint": 1},
    )

    assert result.returncode != 0
    assert "isn't mounted" in result.stderr
    assert not [call for call in calls(tmp_path, "sudo") if call["args"][0] == "install"]


# The password and the repository


def test_the_password_is_made_once_readable_only_by_the_owner(tmp_path: Path, home: Path):
    first = lib(tmp_path, home, 'ensure_backup_password "$PA_BACKUP_PASSWORD_FILE"')
    password = password_file(home).read_text()
    again = lib(tmp_path, home, 'ensure_backup_password "$PA_BACKUP_PASSWORD_FILE"')

    assert first.returncode == 0, first.stderr
    assert again.returncode == 0, again.stderr
    assert len(password.strip()) >= 40
    assert password_file(home).read_text() == password
    assert stat.S_IMODE(password_file(home).stat().st_mode) == 0o600


def init_repository(repository: Path) -> str:
    """The wizard's two steps: make the password, then initialize the repository with it."""
    return (
        'ensure_backup_password "$PA_BACKUP_PASSWORD_FILE"; '
        f'init_backup_repository "{repository}" "$PA_BACKUP_PASSWORD_FILE"'
    )


def restic(home: Path, repository: Path, password: Path, *args: str) -> int:
    return subprocess.run(
        ["restic", *args],
        env={
            "PATH": SYSTEM_PATH,
            "HOME": str(home),
            "RESTIC_REPOSITORY": str(repository),
            "RESTIC_PASSWORD_FILE": str(password),
        },
        capture_output=True,
        check=False,
    ).returncode


@requires_restic
def test_a_repository_is_initialized_once_with_the_password_file(tmp_path: Path, home: Path):
    repository = tmp_path / "drive" / "restic"

    first = lib(tmp_path, home, init_repository(repository), stand_ins=())
    again = lib(tmp_path, home, init_repository(repository), stand_ins=())

    assert first.returncode == 0, first.stderr
    assert again.returncode == 0, again.stderr
    assert "initialized" in first.stderr
    assert "already initialized" in again.stderr
    assert restic(home, repository, password_file(home), "cat", "config") == 0


@requires_restic
def test_a_repository_the_password_doesnt_open_is_refused(tmp_path: Path, home: Path):
    repository = tmp_path / "drive" / "restic"
    other = tmp_path / "other.password"
    other.write_text("not the password\n")
    assert restic(home, repository, other, "init") == 0

    result = lib(tmp_path, home, init_repository(repository), stand_ins=())

    assert result.returncode != 0
    assert "doesn't open" in result.stderr


# The wizard's stages

WIZARD = DEPLOY / "setup-wizard.sh"


def test_the_wizard_mounts_the_drive_and_initializes_the_repository_with_a_saved_password(
    tmp_path: Path, home: Path
):
    # Not mounted yet, then mounted once its unit is started; no repository yet.
    exits: dict[str, int | list[int]] = {
        "mountpoint -q /mnt/pa-backup": [1, 0],
        "restic cat config": 10,
    }
    # Start; the drive's UUID; go on; don't show the password; it's saved; done.
    answers = "\n".join(["", UUID, "", "n", "y", ""]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--backup"], stdin=answers, exits=exits)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Stage 1/2" in result.stdout
    assert "Stage 2/2" in result.stdout
    tee = next(call for call in calls(tmp_path, "sudo") if call["args"][0] == "tee")
    assert f"What=/dev/disk/by-uuid/{UUID}\n" in tee["stdin"]
    assert password_file(home).is_file()
    assert stat.S_IMODE(password_file(home).stat().st_mode) == 0o600
    init = [call for call in calls(tmp_path, "restic") if call["args"] == ["init"]]
    assert init == [
        {
            "name": "restic",
            "args": ["init"],
            "RESTIC_REPOSITORY": f"{MOUNT}/restic",
            "RESTIC_PASSWORD_FILE": str(password_file(home)),
        }
    ]
    assert "password manager" in result.stdout
    # Asked not to show it, the wizard doesn't.
    assert password_file(home).read_text().strip() not in result.stdout


def test_the_wizard_shows_the_password_only_when_asked_and_reminds_to_save_it(
    tmp_path: Path, home: Path
):
    password_file(home).parent.mkdir(parents=True)
    password_file(home).write_text("the-saved-password\n")
    password_file(home).chmod(0o600)
    # Start; go on; show the password; not saved yet; done.
    answers = "\n".join(["", "", "y", "n", ""]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--backup"], stdin=answers)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "the-saved-password" in result.stdout
    assert "save the restic password in your password manager" in result.stdout
    # Mounted and initialized already: nothing to install or initialize.
    assert calls(tmp_path, "sudo") == []
    assert not [call for call in calls(tmp_path, "restic") if call["args"] == ["init"]]
    assert password_file(home).read_text() == "the-saved-password\n"


# backup.sh


def test_backup_sh_runs_pa_backup_against_the_drives_repository_with_the_password_file(
    tmp_path: Path, home: Path
):
    result = run(
        tmp_path,
        home,
        ["bash", str(DEPLOY / "backup.sh")],
        environ={"RESTIC_PASSWORD": "from the environment"},
    )

    assert result.returncode == 0, result.stderr
    assert calls(tmp_path, "uv") == [
        {
            "name": "uv",
            "args": ["pa", "backup"],
            "RESTIC_REPOSITORY": f"{MOUNT}/restic",
            "RESTIC_PASSWORD_FILE": str(password_file(home)),
            "PA_DATA_DIR": str(home / ".local" / "share" / "pa-home-synthetic"),
        }
    ]
