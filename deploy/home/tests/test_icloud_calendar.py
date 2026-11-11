"""iCloud Calendar's setup on the devbox: the wizard's stages that save the app-specific password
(never the Apple ID password) and the calendars to capture, and calendar.sh, which runs
`pa calendar` with them. They run here with HOME in a temporary directory and a stand-in for uv,
which records how it was called and with which iCloud variables, so nothing reaches iCloud."""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
WIZARD = DEPLOY / "setup-wizard.sh"
APPLE_ID = "argus@example.com"
APP_PASSWORD = "qrst-uvwx-yzab-cdef"

STAND_IN = """\
import json, os, sys
args = sys.argv[1:]
call = {"args": args[args.index("--project") + 2:]}
for var in ("PA_ICLOUD_USERNAME", "PA_ICLOUD_PASSWORD_FILE", "PA_ICLOUD_CALENDARS", "PA_TIME_ZONE"):
    if var in os.environ:
        call[var] = os.environ[var]
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(call) + "\\n")
sys.exit(int(os.environ.get("UV_EXIT", "0")))
"""


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def run(
    tmp_path: Path, home: Path, command: list[str], *, stdin: str = "", uv_exit: int = 0
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(f"#!{sys.executable}\n{STAND_IN}")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(tmp_path / "calls"),
            "UV_EXIT": str(uv_exit),
        },
        check=False,
    )


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "calls"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def password_file(home: Path) -> Path:
    return home / ".config" / "pa" / "icloud-app-password"


def icloud_env(home: Path) -> Path:
    return home / ".config" / "pa" / "icloud.env"


def test_the_wizard_saves_the_app_specific_password_and_the_calendars_then_checks_them(
    tmp_path: Path, home: Path
):
    # Start; the Apple ID; the password; go on; the calendars; go on; go on.
    answers = "\n".join(["", APPLE_ID, APP_PASSWORD, "", "Home,Family Calendar", "", ""]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--icloud"], stdin=answers)

    assert result.returncode == 0, result.stdout + result.stderr
    assert [f"Stage {n}/3" in result.stdout for n in (1, 2, 3)] == [True] * 3
    assert "Not your Apple ID password" in result.stdout
    assert password_file(home).read_text() == f"{APP_PASSWORD}\n"
    assert stat.S_IMODE(password_file(home).stat().st_mode) == 0o600
    assert icloud_env(home).read_text() == (
        f'PA_ICLOUD_USERNAME="{APPLE_ID}"\nPA_ICLOUD_CALENDARS="Home,Family Calendar"\n'
    )
    assert stat.S_IMODE(icloud_env(home).stat().st_mode) == 0o600
    assert APP_PASSWORD not in result.stdout + result.stderr
    # The credential is checked by listing the calendars, before and while choosing them.
    listed = {
        "args": ["pa", "calendar", "calendars"],
        "PA_ICLOUD_USERNAME": APPLE_ID,
        "PA_ICLOUD_PASSWORD_FILE": str(password_file(home)),
    }
    assert calls(tmp_path) == [listed, listed]
    # Without a seeded data root there's no time zone, so the dry run is left for later.
    assert "iCloud Calendar dry run: needs" in result.stdout


@pytest.mark.parametrize("password", ["Hunter2-correct-horse!", "abcd efgh ijkl mnop", ""])
def test_anything_but_an_app_specific_password_is_refused_and_nothing_is_saved(
    tmp_path: Path, home: Path, password: str
):
    answers = "\n".join(["", APPLE_ID, password]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--icloud"], stdin=answers)

    assert result.returncode == 1
    assert "that isn't an app-specific password" in result.stdout
    assert "generate an app-specific password" in result.stdout
    assert not password_file(home).exists()
    assert not icloud_env(home).exists()
    assert calls(tmp_path) == []
    if password:
        assert password not in result.stdout + result.stderr


def test_a_password_icloud_refuses_stops_the_wizard(tmp_path: Path, home: Path):
    answers = "\n".join(["", APPLE_ID, APP_PASSWORD, ""]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--icloud"], stdin=answers, uv_exit=1)

    assert result.returncode == 1
    assert "failed" in result.stdout
    assert "Stage 2/3" not in result.stdout


def test_a_saved_password_is_kept_unless_the_owner_asks_for_a_new_one(tmp_path: Path, home: Path):
    password_file(home).parent.mkdir(parents=True)
    password_file(home).write_text("wxyz-wxyz-wxyz-wxyz\n")
    password_file(home).chmod(0o600)
    icloud_env(home).write_text(f'PA_ICLOUD_USERNAME="{APPLE_ID}"\nPA_ICLOUD_CALENDARS="Home"\n')
    # Start; keep the password; go on; keep the calendars; go on; go on.
    answers = "\n".join(["", "n", "", "", "", ""]) + "\n"

    result = run(tmp_path, home, ["bash", str(WIZARD), "--icloud"], stdin=answers)

    assert result.returncode == 0, result.stdout + result.stderr
    assert password_file(home).read_text() == "wxyz-wxyz-wxyz-wxyz\n"
    assert icloud_env(home).read_text() == (
        f'PA_ICLOUD_USERNAME="{APPLE_ID}"\nPA_ICLOUD_CALENDARS="Home"\n'
    )


def test_calendar_sh_runs_the_dry_run_with_icloud_env_and_the_owners_time_zone(
    tmp_path: Path, home: Path
):
    icloud_env(home).parent.mkdir(parents=True)
    icloud_env(home).write_text(f'PA_ICLOUD_USERNAME="{APPLE_ID}"\nPA_ICLOUD_CALENDARS="Home"\n')
    owner_env = home / ".local" / "share" / "pa-home" / "owner.env"
    owner_env.parent.mkdir(parents=True)
    owner_env.write_text('PA_TIME_ZONE="America/Chicago"\n')

    command = ["bash", str(DEPLOY / "calendar.sh"), "dry-run", "--window-start", "2026-08-26"]
    result = run(tmp_path, home, command)

    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [
        {
            "args": ["pa", "calendar", "dry-run", "--window-start", "2026-08-26"],
            "PA_ICLOUD_USERNAME": APPLE_ID,
            "PA_ICLOUD_PASSWORD_FILE": str(password_file(home)),
            "PA_ICLOUD_CALENDARS": "Home",
            "PA_TIME_ZONE": "America/Chicago",
        }
    ]


def test_calendar_sh_needs_the_wizard_first(tmp_path: Path, home: Path):
    result = run(tmp_path, home, ["bash", str(DEPLOY / "calendar.sh"), "calendars"])

    assert result.returncode == 1
    assert "run setup-wizard.sh --icloud first" in result.stderr
    assert calls(tmp_path) == []


@pytest.mark.parametrize("value", ['Home"', "Home$HOME", "Home`id`", "Home\\n"])
def test_a_value_that_would_break_out_of_its_quotes_is_refused(
    tmp_path: Path, home: Path, value: str
):
    script = f'source "{LIB}"; set_env_value "$HOME/x.env" PA_ICLOUD_CALENDARS {shlex.quote(value)}'

    result = run(tmp_path, home, ["bash", "-c", script])

    assert result.returncode == 1
    assert not (home / "x.env").exists()
