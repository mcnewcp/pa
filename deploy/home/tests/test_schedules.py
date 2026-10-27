"""The schedules on the devbox (#98): the capture cycle every 15 minutes and the nightly job
overnight, as system units and timers run as the devbox user, both catching up after the devbox
was off, and confined to what they write. These run here with HOME in a temporary directory and
stand-ins for `pa`, sudo and systemctl; nothing is installed on the host. `systemd-analyze`
checks the units and computes the timers' schedules where it's installed."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
UNITS = ("pa-capture.service", "pa-capture.timer", "pa-nightly.service", "pa-nightly.timer")
BACKUP_MOUNT_UNIT = r"mnt-pa\x2dbackup.mount"

requires_systemd_analyze = pytest.mark.skipif(
    shutil.which("systemd-analyze") is None, reason="needs systemd-analyze"
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return home


def lib(script: str, home: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "USER": "owner", "PATH": "/usr/bin:/bin", **(env or {})},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def value(name: str, home: Path) -> str:
    return lib(f'printf %s "${name}"', home)


def rendered(name: str, home: Path) -> str:
    return lib(f"render_schedule_unit {name}", home)


def settings(name: str, home: Path) -> dict[str, list[str]]:
    """The rendered unit's settings: each key's values, in order, whatever its section; a
    space-separated list setting given several times is one list."""
    found: dict[str, list[str]] = {}
    for line in rendered(name, home).splitlines():
        if "=" in line and not line.startswith("#"):
            key, setting = line.split("=", 1)
            found.setdefault(key, []).extend(setting.split())
    return found


def writable(name: str, home: Path) -> set[str]:
    """What the service may write, as paths ("-" only lets a missing one be)."""
    return {path.removeprefix("-") for path in settings(name, home)["ReadWritePaths"]}


# The schedules


def next_elapses(calendar: str, count: int) -> list[datetime]:
    result = subprocess.run(
        ["systemd-analyze", "calendar", f"--iterations={count}", calendar],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
    )
    return [
        datetime.strptime(" ".join(line.split(":", 1)[1].split()[1:3]), "%Y-%m-%d %H:%M:%S")
        for line in result.stdout.splitlines()
        if "Next elapse:" in line or line.strip().startswith("Iteration #")
    ]


@requires_systemd_analyze
def test_the_capture_cycle_starts_every_15_minutes_on_the_quarter_hour(home: Path):
    [calendar] = settings("pa-capture.timer", home)["OnCalendar"]

    elapses = next_elapses(calendar, 9)

    assert len(elapses) == 9
    assert {b - a for a, b in pairwise(elapses)} == {timedelta(minutes=15)}
    assert {moment.minute for moment in elapses} <= {0, 15, 30, 45}


@requires_systemd_analyze
def test_the_nightly_job_starts_once_a_day_overnight(home: Path):
    [day, time] = settings("pa-nightly.timer", home)["OnCalendar"]

    elapses = next_elapses(f"{day} {time}", 4)

    assert len(elapses) == 4
    assert {b - a for a, b in pairwise(elapses)} == {timedelta(days=1)}
    assert all(1 <= moment.hour < 5 for moment in elapses)


@pytest.mark.parametrize(
    ("timer", "service"),
    [("pa-capture.timer", "pa-capture.service"), ("pa-nightly.timer", "pa-nightly.service")],
)
def test_each_timer_catches_up_after_the_devbox_was_off_and_never_piles_up(
    home: Path, timer: str, service: str
):
    """Persistent: a start missed while the devbox was off happens at boot. A oneshot service is
    still starting until its job exits, so the timer starts no second one meanwhile."""
    timer_settings = settings(timer, home)

    assert timer_settings["Persistent"] == ["true"]
    assert timer_settings["Unit"] == [service]
    assert timer_settings["WantedBy"] == ["timers.target"]
    assert settings(service, home)["Type"] == ["oneshot"]


def test_the_nightly_job_waits_for_a_capture_cycle_under_way_rather_than_skip_the_night(
    home: Path,
):
    """At boot both timers catch up at once: the cycle runs first, then the night's job."""
    assert "pa-capture.service" in settings("pa-nightly.service", home)["After"]
    assert "pa-nightly.service" not in settings("pa-capture.service", home).get("After", [])


def test_the_nightly_job_wants_the_backup_drive_but_runs_without_it(home: Path):
    """Without the drive the job still runs, and the backup records the missing repository."""
    nightly = settings("pa-nightly.service", home)

    assert BACKUP_MOUNT_UNIT in nightly["Wants"]
    assert BACKUP_MOUNT_UNIT in nightly["After"]
    assert "Requires" not in nightly and "RequiresMountsFor" not in nightly
    assert "network-online.target" in nightly["After"]
    assert "incus.service" in nightly["After"]


def test_the_jobs_run_as_the_devbox_user_from_this_checkout(home: Path):
    capture, nightly = settings("pa-capture.service", home), settings("pa-nightly.service", home)

    assert capture["User"] == nightly["User"] == ["owner"]
    assert capture["ExecStart"] == [str(DEPLOY / "capture.sh")]
    assert nightly["ExecStart"] == [str(DEPLOY / "nightly.sh")]


# Confinement


def test_the_capture_service_writes_only_the_store_s_capture_paths(home: Path):
    data = value("PA_DATA_ROOT", home)
    capture = settings("pa-capture.service", home)

    assert writable("pa-capture.service", home) == {
        f"{data}/{path}" for path in ("inbox", "l0", "catalog", "state", "status", "pa.lock")
    }
    assert capture["ProtectSystem"] == ["strict"]
    assert capture["ProtectHome"] == ["read-only"]
    assert capture["PrivateTmp"] == ["yes"]
    assert capture["NoNewPrivileges"] == ["yes"]
    assert capture["CapabilityBoundingSet"] == []


@pytest.mark.parametrize("service", ["pa-capture.service", "pa-nightly.service"])
def test_neither_job_can_write_the_credentials_the_vault_copy_or_the_code(home: Path, service: str):
    """Under a read-only home, they read what they're not given to write."""
    read_only = [
        value(name, home)
        for name in (
            "PA_GMAIL_TOKEN",
            "PA_ICLOUD_PASSWORD_FILE",
            "PA_ICLOUD_ENV",
            "PA_BACKUP_PASSWORD_FILE",
            "PA_LIBRARIAN_TOKEN",
            "PA_VAULT",
            "PA_VAULT_ENV",
            "PA_OWNER_ENV",
        )
    ] + [str(DEPLOY.parents[1])]

    # The test's home is under /tmp, which the nightly job writes; the devbox user's isn't.
    for path in writable(service, home) - {"/tmp"}:
        for kept in read_only:
            assert os.path.commonpath([path, kept]) not in (path, kept), (path, kept)
    assert settings(service, home)["ProtectHome"] == ["read-only"]


def test_the_nightly_job_writes_the_wiki_and_the_backup_too(home: Path):
    data = value("PA_DATA_ROOT", home)

    assert writable("pa-nightly.service", home) == writable("pa-capture.service", home) | {
        f"{data}/wiki",
        f"{data}/wiki.git",
        value("PA_BACKUP_REPOSITORY", home),
        value("PA_RESTIC_CACHE", home),
        "/tmp",
    }


def test_the_nightly_job_shares_the_hosts_tmp_for_the_librarians_vm(home: Path):
    """The run renders the librarian project in /tmp, and Incus, outside the unit, shares that
    path into the VM: a private /tmp would share nothing."""
    assert settings("pa-nightly.service", home).get("PrivateTmp", ["no"]) == ["no"]


@requires_systemd_analyze
def test_systemd_accepts_the_units(tmp_path: Path, home: Path):
    units = tmp_path / "units"
    units.mkdir()
    for name in UNITS:
        text = rendered(name, home).replace("User=owner", f"User={os.getuid()}")
        (units / name).write_text(text)

    result = subprocess.run(
        ["systemd-analyze", "verify", *(str(units / name) for name in UNITS)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for name in UNITS:
        assert name not in result.stderr  # no warnings about them either


# The jobs' scripts

# Records its arguments and the environment it was given, as `pa` would see it.
STAND_IN_PA = """\
import json, os, sys
with open(os.environ["PA_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
"""


@pytest.fixture
def instance(home: Path, tmp_path: Path) -> dict[str, str]:
    """An instance set up as the wizard leaves it, with a stand-in `pa`."""
    owner_env = Path(value("PA_OWNER_ENV", home))
    owner_env.parent.mkdir(parents=True)
    owner_env.write_text("PA_OWNER_NAME='Argus McNevans'\nPA_TIME_ZONE=America/Chicago\n")
    vault_env = Path(value("PA_VAULT_ENV", home))
    vault_env.parent.mkdir(parents=True, exist_ok=True)
    vault_env.write_text('PA_VAULT_DIR="/vault"\nPA_VAULT_DAILY_NOTES="Daily"\n')
    Path(value("PA_ICLOUD_ENV", home)).write_text(
        'PA_ICLOUD_USERNAME="argus@example.com"\nPA_ICLOUD_CALENDARS="Home,Work"\n'
    )
    pa = tmp_path / "pa"
    pa.write_text(f"#!{sys.executable}\n{STAND_IN_PA}")
    pa.chmod(pa.stat().st_mode | stat.S_IXUSR)
    return {"PA_BIN": str(pa), "PA_LOG": str(tmp_path / "pa.jsonl")}


def run_job(script: str, home: Path, env: dict[str, str], *args: str) -> dict:
    result = subprocess.run(
        [str(DEPLOY / script), *args],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "USER": "owner", "PATH": "/usr/bin:/bin", **env},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    [call] = [json.loads(line) for line in Path(env["PA_LOG"]).read_text().splitlines()]
    return call


def test_the_capture_job_runs_a_cycle_with_the_instances_configuration(
    home: Path, instance: dict[str, str]
):
    call = run_job("capture.sh", home, instance)

    env = call["env"]
    assert call["argv"] == ["capture"]
    assert env["PA_DATA_DIR"] == value("PA_DATA_ROOT", home)
    assert (env["PA_OWNER_NAME"], env["PA_TIME_ZONE"]) == ("Argus McNevans", "America/Chicago")
    assert (env["PA_VAULT_DIR"], env["PA_VAULT_DAILY_NOTES"]) == ("/vault", "Daily")
    assert env["PA_ICLOUD_CALENDARS"] == "Home,Work"
    assert env["PA_ICLOUD_PASSWORD_FILE"] == value("PA_ICLOUD_PASSWORD_FILE", home)
    # Gmail only once its token is saved.
    assert "PA_GMAIL_TOKEN" not in env


def test_the_capture_job_collects_gmail_once_its_token_is_saved(
    home: Path, instance: dict[str, str]
):
    token = Path(value("PA_GMAIL_TOKEN", home))
    token.write_text("{}")

    call = run_job("capture.sh", home, instance, "--backfill", "2026-08-26")

    assert call["argv"] == ["capture", "--backfill", "2026-08-26"]
    assert call["env"]["PA_GMAIL_TOKEN"] == str(token)


def test_the_nightly_job_runs_with_its_own_sandbox_and_backup_repository(
    home: Path, instance: dict[str, str]
):
    """Never a librarian command or restic variables from the environment, which could run
    claude elsewhere or point the backup elsewhere."""
    elsewhere = {
        "PA_LIBRARIAN_COMMAND": "claude",
        "RESTIC_PASSWORD": "hunter2",
        "RESTIC_REPOSITORY": "/elsewhere",
        "RESTIC_PASSWORD_COMMAND": "cat /elsewhere",
        "RESTIC_REPOSITORY_FILE": "/elsewhere",
    }

    call = run_job("nightly.sh", home, {**instance, **elsewhere})

    env = call["env"]
    assert call["argv"] == ["nightly"]
    assert env["PA_DATA_DIR"] == value("PA_DATA_ROOT", home)
    assert env["PA_TIME_ZONE"] == "America/Chicago"
    assert env["PA_ICLOUD_CALENDARS"] == "Home,Work"
    assert env["PA_LIBRARIAN_SANDBOX"] == str(DEPLOY / "librarian-sandbox.sh")
    assert env["RESTIC_REPOSITORY"] == value("PA_BACKUP_REPOSITORY", home)
    assert env["RESTIC_PASSWORD_FILE"] == value("PA_BACKUP_PASSWORD_FILE", home)
    for name in ("PA_LIBRARIAN_COMMAND", "RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND"):
        assert name not in env
    assert "RESTIC_REPOSITORY_FILE" not in env


@pytest.mark.parametrize("script", ["capture.sh", "nightly.sh"])
def test_a_job_refuses_an_instance_without_its_owner(home: Path, tmp_path: Path, script: str):
    result = subprocess.run(
        [str(DEPLOY / script)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "USER": "owner", "PATH": "/usr/bin:/bin"},
        check=False,
    )

    assert result.returncode == 1
    assert "owner.env is missing" in result.stderr


# Installing and enabling them


@pytest.fixture
def installer(stand_ins: Any, home: Path, instance: dict[str, str]) -> Any:
    """Stand-ins for sudo and systemctl (conftest.py), in the instance's home."""
    stand_ins.add("systemctl")
    assert stand_ins.home == home
    return stand_ins


def install(stand_ins: Any, instance: dict[str, str], *args: str):
    systemd = stand_ins.root / "systemd"
    systemd.mkdir(exist_ok=True)
    return stand_ins.run(
        DEPLOY / "install-schedules.sh",
        *args,
        env={**instance, "PA_SYSTEMD_DIR": str(systemd)},
    )


def test_installing_puts_each_unit_in_place_and_reloads_systemd(
    installer: Any, instance: dict[str, str], home: Path
):
    result = install(installer, instance)

    assert result.returncode == 0, result.stderr
    systemd = installer.root / "systemd"
    installed = [call.argv for call in installer.of("sudo") if call.argv[:1] == ["install"]]
    assert [argv[-1] for argv in installed] == [str(systemd / name) for name in UNITS]
    assert all(argv[1:3] == ["-m", "0644"] for argv in installed)
    assert ["systemctl", "daemon-reload"] in [call.argv for call in installer.of("sudo")]
    assert not any("enable" in call.argv for call in installer.of("sudo"))
    # What the services may write exists before they start.
    data = Path(value("PA_DATA_ROOT", home))
    for path in ("inbox", "l0", "catalog", "state", "status"):
        assert (data / path).is_dir(), path
    assert (data / "pa.lock").is_file()


def test_enabling_starts_both_timers_once_the_wiki_is_seeded(
    installer: Any, instance: dict[str, str], home: Path
):
    Path(value("PA_WIKI_GIT", home)).mkdir(parents=True)

    result = install(installer, instance, "--enable")

    assert result.returncode == 0, result.stderr
    assert ["systemctl", "enable", "--now", "pa-capture.timer", "pa-nightly.timer"] in [
        call.argv for call in installer.of("sudo")
    ]


def test_enabling_is_refused_until_the_wiki_is_seeded(installer: Any, instance: dict[str, str]):
    """A cycle between the go-live backfill and the seed would be replayed and offered again."""
    result = install(installer, instance, "--enable")

    assert result.returncode == 1
    assert "the wiki isn't seeded yet" in result.stderr
    assert not any("enable" in call.argv for call in installer.of("sudo"))


def test_installing_needs_the_checkouts_venv(installer: Any, instance: dict[str, str]):
    result = install(installer, {**instance, "PA_BIN": "/nonexistent/pa"})

    assert result.returncode == 1
    assert "run uv sync" in result.stderr
    assert installer.of("sudo") == []


# Checking them


def check(installer: Any, instance: dict[str, str], nightly_result: str):
    installer.answer("systemctl", "pa-nightly.service", "Result", stdout=f"{nightly_result}\n")
    installer.answer("systemctl", "Result", stdout="success\n")
    installer.answer("systemctl", "ExecMainStatus", stdout="0\n")
    installer.answer("systemctl", "LastTriggerUSec", stdout="Mon 2026-10-26 03:00:00 CDT\n")
    installer.answer("systemctl", "NextElapse", stdout="Tue 2026-10-27 03:00:00 CDT\n")
    return installer.run(DEPLOY / "check-schedules.sh", env=instance)


def test_the_check_passes_with_both_timers_waiting_and_each_jobs_last_run_succeeded(
    installer: Any, instance: dict[str, str]
):
    result = check(installer, instance, "success")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert "pa-nightly.timer is enabled and waiting: last started Mon 2026-10-26" in result.stdout
    # Then pa status, with the instance's configuration.
    [status] = [json.loads(line) for line in Path(instance["PA_LOG"]).read_text().splitlines()]
    assert status["argv"] == ["status"]
    assert status["env"]["PA_TIME_ZONE"] == "America/Chicago"


def test_the_check_fails_when_a_jobs_last_run_failed(installer: Any, instance: dict[str, str]):
    result = check(installer, instance, "exit-code")

    assert result.returncode == 1
    assert "FAIL  pa-nightly.service's last run: exit-code" in result.stdout


def test_the_check_fails_when_a_timer_isnt_enabled(installer: Any, instance: dict[str, str]):
    installer.answer("systemctl", "is-enabled", "pa-capture.timer", exit=1)

    result = check(installer, instance, "success")

    assert result.returncode == 1
    assert "FAIL  pa-capture.timer isn't enabled and waiting" in result.stdout
