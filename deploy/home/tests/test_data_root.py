"""The real data root (#99): the deploy default, created empty with the owner's real identity,
and the synthetic data root v0.11 and v0.12 ran on, kept addressable until it is retired. The
scripts run here with HOME in a temporary directory and stand-ins for incus, sudo and the rest,
so nothing touches the host."""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"


def lib(stand_ins: Any, script: str) -> subprocess.CompletedProcess[str]:
    return stand_ins.run("/dev/stdin", stdin=f'set -euo pipefail; source "{LIB}"; {script}\n')


def real_root(stand_ins: Any) -> Path:
    return stand_ins.home / ".local" / "share" / "pa-home"


def test_the_deploy_default_is_the_real_data_root(stand_ins: Any):
    result = lib(stand_ins, 'echo "$PA_DATA_ROOT $PA_OWNER_ENV $PA_L0 $PA_SYNTHETIC_DATA_ROOT"')

    share = stand_ins.home / ".local" / "share"
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        f"{share}/pa-home",
        f"{share}/pa-home/owner.env",
        f"{share}/pa-home/l0",
        f"{share}/pa-home-synthetic",
    ]


# Creating it


def test_the_real_data_root_is_created_empty_and_private(stand_ins: Any):
    result = stand_ins.run(DEPLOY / "create-data-root.sh")

    root = real_root(stand_ins)
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    made = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert made == [
        "catalog",
        "inbox",
        "inbox/assistant_chat",
        "l0",
        "l0/episodes",
        "state",
        "status",
    ]


def test_creating_it_again_keeps_what_it_holds_and_makes_it_private_again(stand_ins: Any):
    root = real_root(stand_ins)
    (root / "l0" / "episodes").mkdir(parents=True)
    (root / "owner.env").write_text("PA_OWNER_NAME='Argus McNevans'\n")
    root.chmod(0o755)

    result = stand_ins.run(DEPLOY / "create-data-root.sh")

    assert result.returncode == 0, result.stderr
    assert (root / "owner.env").read_text() == "PA_OWNER_NAME='Argus McNevans'\n"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


# The owner, from the wizard

WIZARD = DEPLOY / "setup-wizard.sh"
OWNER_ENV = (
    "PA_OWNER_NAME='Argus McNevans'\n"
    "PA_OWNER_EMAILS=argus@example.com,gus@example.net\n"
    "PA_OWNER_OTHER_NAMES=Gus\n"
    "PA_TIME_ZONE=America/Chicago\n"
)


def owner_wizard(stand_ins: Any, *answers: str) -> subprocess.CompletedProcess[str]:
    """Runs `setup-wizard.sh --owner` with the devbox's zone America/Chicago, answering each of
    `answers` in turn (Enter for anything after)."""
    stand_ins.add("timedatectl")
    stand_ins.answer("timedatectl", "Timezone", stdout="America/Chicago\n")
    return stand_ins.run(WIZARD, "--owner", stdin="\n".join(answers) + "\n" * 8)


def sourced(owner_env: Path, *names: str) -> list[str]:
    """The values bash reads from owner.env, as the scripts source it."""
    script = f'source "{owner_env}"; ' + "; ".join(f'printf "%s\\n" "${name}"' for name in names)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def test_the_wizard_creates_the_data_root_and_writes_the_owner_once_confirmed(stand_ins: Any):
    # Start; go on; the name, addresses, other names; the devbox's zone; yes; go on.
    result = owner_wizard(
        stand_ins, "", "", "Argus McNevans", "argus@example.com, gus@example.net", "Gus", "", "y"
    )

    root = real_root(stand_ins)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [f"Stage {n}/2" in result.stdout for n in (1, 2)] == [True] * 2
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert (root / "owner.env").read_text() == OWNER_ENV
    assert stat.S_IMODE((root / "owner.env").stat().st_mode) == 0o600
    # Shown back before it's written.
    shown = result.stdout[: result.stdout.index("Are these right?")]
    for value in ("Argus McNevans", "argus@example.com,gus@example.net", "Gus", "America/Chicago"):
        assert value in shown


def test_any_name_reads_back_as_given(stand_ins: Any):
    name = 'Argus O\'Nevans "Gus" $HOME `id`'
    result = owner_wizard(stand_ins, "", "", name, "argus@example.com", "", "Europe/Paris", "y")

    owner_env = real_root(stand_ins) / "owner.env"
    assert result.returncode == 0, result.stdout + result.stderr
    assert sourced(owner_env, "PA_OWNER_NAME", "PA_OWNER_OTHER_NAMES", "PA_TIME_ZONE") == [
        name,
        "",
        "Europe/Paris",
    ]


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        (("", "argus@example.com", "", ""), "a name"),
        (("Argus McNevans", "", "", ""), "an email address"),
        (("Argus McNevans", "argus@example.com, gus", "", ""), "isn't an email address"),
        (("Argus McNevans", "argus@example.com", "", "Mars/Olympus_Mons"), "IANA time zone"),
        (("Argus McNevans", "argus@example.com", "", "localtime"), "IANA time zone"),
        (("Argus McNevans", "argus@example.com", "", "../../etc/passwd"), "IANA time zone"),
    ],
)
def test_a_value_that_wont_do_is_refused_and_nothing_is_written(
    stand_ins: Any, answers: tuple[str, ...], reason: str
):
    result = owner_wizard(stand_ins, "", "", *answers, "y")

    assert result.returncode == 1
    assert reason in result.stdout
    assert not (real_root(stand_ins) / "owner.env").exists()


def test_values_the_owner_doesnt_confirm_are_not_written(stand_ins: Any):
    result = owner_wizard(stand_ins, "", "", "Argus McNevans", "argus@example.com", "", "", "n")

    assert result.returncode == 1
    assert "nothing was written" in result.stdout
    assert not (real_root(stand_ins) / "owner.env").exists()


def test_a_written_owner_is_kept_unless_the_owner_asks_to_rewrite_it(stand_ins: Any):
    root = real_root(stand_ins)
    root.mkdir(parents=True)
    (root / "owner.env").write_text(OWNER_ENV)

    kept = owner_wizard(stand_ins, "", "", "n")

    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert (root / "owner.env").read_text() == OWNER_ENV

    rewritten = owner_wizard(
        stand_ins, "", "", "y", "Argus McNevans", "argus@example.com", "", "", "y"
    )

    assert rewritten.returncode == 0, rewritten.stdout + rewritten.stderr
    assert sourced(root / "owner.env", "PA_OWNER_EMAILS") == ["argus@example.com"]


# Leaving the synthetic data root (the go-live)

OWN_FILESYSTEMS = ("inbox/assistant_chat", "wiki", "replays")


def synthetic_root(stand_ins: Any) -> Path:
    return stand_ins.home / ".local" / "share" / "pa-home-synthetic"


def disk(source: Path | str, path: str, readonly: bool = True) -> dict[str, str]:
    return {"type": "disk", "source": str(source), "path": path, "readonly": str(readonly).lower()}


def store_devices(root: Path) -> dict[str, dict[str, str]]:
    """pa-home's store devices, as mount-store.sh adds them over `root`."""
    return {
        "pa-l0": disk(root / "l0", "/srv/pa/l0"),
        "pa-chat-inbox": disk(
            root / "inbox" / "assistant_chat", "/srv/pa/inbox/assistant_chat", False
        ),
        "pa-wiki": disk(root / "wiki", "/srv/pa/wiki"),
        "pa-status": disk(root / "status", "/srv/pa/status"),
    }


def unit_name(path: Path) -> str:
    return subprocess.run(
        ["systemd-escape", "-p", "--suffix=mount", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def installed_units(stand_ins: Any, root: Path) -> Path:
    """A systemd directory holding `root`'s own-filesystem mount units, as install-own-filesystem.sh
    leaves them."""
    systemd = stand_ins.root / "systemd"
    systemd.mkdir(exist_ok=True)
    for directory in OWN_FILESYSTEMS:
        (systemd / unit_name(root / directory)).write_text("[Mount]\n")
    return systemd


def vms(*vms: tuple[str, dict[str, dict[str, str]]]) -> str:
    """Every VM, as `incus query /1.0/instances?recursion=1` prints them."""
    return json.dumps([{"name": name, "expanded_devices": devices} for name, devices in vms])


def release(stand_ins: Any, systemd: Path) -> subprocess.CompletedProcess[str]:
    return stand_ins.run(
        "/dev/stdin",
        env={"PA_SYSTEMD_DIR": str(systemd)},
        stdin=f'set -euo pipefail; source "{LIB}"; release_data_root "$PA_SYNTHETIC_DATA_ROOT"\n',
    )


def test_going_live_takes_the_vm_off_the_synthetic_data_root_then_unmounts_its_filesystems(
    stand_ins: Any,
):
    synthetic, real = synthetic_root(stand_ins), real_root(stand_ins)
    systemd = installed_units(stand_ins, synthetic)
    root_disk = {"root": {"type": "disk", "pool": "pa", "path": "/"}}
    on_synthetic = {**root_disk, **store_devices(synthetic)}
    stand_ins.answer(
        "incus",
        "query /1.0/instances/pa-home",
        stdout=json.dumps({"name": "pa-home", "devices": on_synthetic}),
    )
    stand_ins.answer(
        "incus", "query /1.0/instances?recursion=1", stdout=vms(("pa-home", root_disk))
    )

    result = release(stand_ins, systemd)

    assert result.returncode == 0, result.stderr
    lines = [call.line for call in stand_ins.calls]
    removed = [line for line in lines if line.startswith("incus config device remove")]
    assert sorted(removed) == sorted(
        f"incus config device remove pa-home {device}" for device in store_devices(real)
    )
    # Remote Control stops first, so no exchange is captured to the VM's own disk meanwhile.
    stop = lines.index("incus exec pa-home -- systemctl stop pa-remote-control.service")
    assert stop < lines.index(removed[0])
    disabled = [line for line in lines if line.startswith("sudo systemctl disable --now")]
    assert sorted(disabled) == sorted(
        f"sudo systemctl disable --now {unit_name(synthetic / d)}" for d in OWN_FILESYSTEMS
    )
    assert lines.index(removed[-1]) < lines.index(disabled[0])
    # The synthetic data root itself stays, until it's retired.
    assert not [line for line in lines if " rm " in f" {line} "]


def test_going_live_onto_a_synthetic_data_root_is_refused(stand_ins: Any):
    synthetic = synthetic_root(stand_ins)

    result = stand_ins.run(WIZARD, "--go-live", env={"PA_DATA_ROOT": str(synthetic)}, stdin="\n")

    assert result.returncode == 2
    assert "unset PA_DATA_ROOT" in result.stderr
    assert not synthetic.exists()
    assert stand_ins.calls == []


def test_a_data_root_another_vm_is_still_shared_isnt_left(stand_ins: Any):
    synthetic = synthetic_root(stand_ins)
    systemd = installed_units(stand_ins, synthetic)
    stand_ins.answer(
        "incus",
        "query /1.0/instances/pa-home",
        stdout=json.dumps({"name": "pa-home", "devices": {}}),
    )
    stand_ins.answer(
        "incus",
        "query /1.0/instances?recursion=1",
        stdout=vms(("pa-librarian", {"lib-wiki": disk(synthetic / "wiki", "/srv/pa/wiki", False)})),
    )

    result = release(stand_ins, systemd)

    assert result.returncode != 0
    assert f"pa-librarian is shared {synthetic}" in result.stderr
    assert not [c for c in stand_ins.calls if c.line.startswith("sudo systemctl disable")]


def test_a_data_root_already_left_is_left_alone(stand_ins: Any):
    stand_ins.answer(
        "incus",
        "query /1.0/instances/pa-home",
        stdout=json.dumps({"name": "pa-home", "devices": {}}),
    )
    stand_ins.answer("incus", "query /1.0/instances?recursion=1", stdout=vms(("pa-home", {})))

    result = release(stand_ins, stand_ins.root / "systemd")

    assert result.returncode == 0, result.stderr
    assert [c.line for c in stand_ins.calls if not c.line.startswith("incus query")] == []


# Retiring it

RETIRE = DEPLOY / "retire-data-root.sh"


def seeded(root: Path) -> Path:
    (root / "l0" / "episodes").mkdir(parents=True)
    (root / "owner.env").write_text("PA_OWNER_NAME='Argus McNevans'\n")
    return root


def retire(
    stand_ins: Any,
    path: Path | str,
    systemd: Path,
    *,
    shared: str = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stand_ins.answer("incus", "query /1.0/instances?recursion=1", stdout=shared or vms())
    return stand_ins.run(RETIRE, path, env={"PA_SYSTEMD_DIR": str(systemd), **(env or {})})


def test_a_synthetic_data_root_no_vm_shares_is_retired_with_its_mount_units(stand_ins: Any):
    synthetic = seeded(synthetic_root(stand_ins))
    systemd = installed_units(stand_ins, synthetic)

    result = retire(stand_ins, synthetic, systemd)

    assert result.returncode == 0, result.stderr
    assert not synthetic.exists()
    lines = [call.line for call in stand_ins.calls]
    for directory in OWN_FILESYSTEMS:
        unit = unit_name(synthetic / directory)
        assert f"sudo systemctl disable --now {unit}" in lines
        assert f"sudo rm -f {systemd / unit}" in lines
    assert "sudo systemctl daemon-reload" in lines


def test_a_data_root_already_retired_is_left_retired(stand_ins: Any):
    result = retire(stand_ins, synthetic_root(stand_ins), stand_ins.root / "systemd")

    assert result.returncode == 0, result.stderr
    assert "already retired" in result.stderr


def test_a_synthetic_data_root_a_vm_still_shares_is_kept(stand_ins: Any):
    synthetic = seeded(synthetic_root(stand_ins))
    systemd = installed_units(stand_ins, synthetic)

    result = retire(
        stand_ins, synthetic, systemd, shared=vms(("pa-home", store_devices(synthetic)))
    )

    assert result.returncode != 0
    assert f"pa-home is shared {synthetic}" in result.stderr
    assert (synthetic / "owner.env").exists()
    assert not [c for c in stand_ins.calls if c.prog == "sudo"]


@pytest.mark.parametrize(
    ("name", "env"),
    [
        ("pa-home", {}),  # the real data root
        ("pa-home-synthetic", {"PA_DATA_ROOT": "{share}/pa-home-synthetic"}),  # the active one
        ("pa-home-real", {}),
        ("notes", {}),
    ],
)
def test_anything_but_an_inactive_synthetic_data_root_is_refused(
    stand_ins: Any, name: str, env: dict[str, str]
):
    share = stand_ins.home / ".local" / "share"
    root = seeded(share / name)
    env = {key: value.replace("{share}", str(share)) for key, value in env.items()}

    result = retire(stand_ins, root, installed_units(stand_ins, root), env=env)

    assert result.returncode != 0
    assert "retires only" in result.stderr
    assert (root / "owner.env").exists()
    assert not [c for c in stand_ins.calls if c.prog == "sudo"]


def test_a_link_named_like_the_synthetic_data_root_leading_to_the_real_one_is_refused(
    stand_ins: Any,
):
    real = seeded(real_root(stand_ins))
    synthetic_root(stand_ins).symlink_to(real)

    result = retire(stand_ins, synthetic_root(stand_ins), stand_ins.root / "systemd")

    assert result.returncode != 0
    assert (real / "owner.env").exists()
