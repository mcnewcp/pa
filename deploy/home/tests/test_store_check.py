"""The store check's view of what the devbox shares with the VM: exactly L0, the chat inbox,
the wiki and the status, with all but the chat inbox read-only. It reads the VM's devices as
Incus reports them (`incus query /1.0/instances/pa-home`), so it runs here on stand-in device
lists."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib.sh"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def data_root(home: Path) -> Path:
    return home / ".local" / "share" / "pa-home"


def disk(source: Path | str, path: str, readonly: bool | None = None) -> dict[str, str]:
    device = {"type": "disk", "source": str(source), "path": path}
    if readonly is not None:
        device["readonly"] = str(readonly).lower()
    return device


def the_store(home: Path) -> dict[str, dict[str, str]]:
    root = data_root(home)
    return {
        "root": {"type": "disk", "pool": "pa", "path": "/"},
        "eth0": {"type": "nic", "network": "pabr0"},
        "pa-l0": disk(root / "l0", "/srv/pa/l0", True),
        "pa-chat-inbox": disk(root / "inbox" / "assistant_chat", "/srv/pa/inbox/assistant_chat"),
        "pa-wiki": disk(root / "wiki", "/srv/pa/wiki", True),
        "pa-status": disk(root / "status", "/srv/pa/status", True),
    }


def problems(home: Path, devices: dict[str, Any]) -> list[str]:
    instance = {"name": "pa-home", "expanded_devices": devices}
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}" && store_share_problems'],
        input=json.dumps(instance),
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    return result.stdout.splitlines()


def test_the_four_shares_as_mounted_have_no_problems(home: Path):
    assert problems(home, the_store(home)) == []


@pytest.mark.parametrize(("device", "directory"), [("pa-wiki", "wiki"), ("pa-status", "status")])
def test_a_missing_wiki_or_status_share_is_a_problem(home: Path, device: str, directory: str):
    devices = the_store(home)
    del devices[device]

    assert problems(home, devices) == [f"{data_root(home) / directory} isn't shared"]


@pytest.mark.parametrize(
    ("device", "directory"), [("pa-wiki", "wiki"), ("pa-l0", "l0"), ("pa-status", "status")]
)
def test_a_writable_wiki_l0_or_status_share_is_a_problem(home: Path, device: str, directory: str):
    devices = the_store(home)
    devices[device]["readonly"] = "false"

    assert problems(home, devices) == [f"{data_root(home) / directory} is shared writable"]


def test_a_share_without_a_readonly_setting_is_writable(home: Path):
    devices = the_store(home)
    del devices["pa-wiki"]["readonly"]

    assert problems(home, devices) == [f"{data_root(home) / 'wiki'} is shared writable"]


def test_the_status_file_shared_alone_rather_than_its_directory_is_a_problem(home: Path):
    """The status file is replaced by a rename, which a share of the file itself would miss."""
    devices = the_store(home)
    status = data_root(home) / "status"
    devices["pa-status"] = disk(status / "status.json", "/srv/pa/status/status.json", True)

    assert problems(home, devices) == [
        f"{status} isn't shared",
        f"{status / 'status.json'} is shared too, at /srv/pa/status/status.json",
    ]


def test_a_share_at_another_path_in_the_vm_is_a_problem(home: Path):
    devices = the_store(home)
    devices["pa-wiki"]["path"] = "/mnt/wiki"

    assert problems(home, devices) == [
        f"{data_root(home) / 'wiki'} is shared at /mnt/wiki, not /srv/pa/wiki"
    ]


def test_the_wikis_history_shared_is_a_problem(home: Path):
    devices = the_store(home)
    devices["pa-wiki-history"] = disk(data_root(home) / "wiki.git", "/srv/pa/wiki.git", True)

    assert problems(home, devices) == [
        f"{data_root(home) / 'wiki.git'} is shared too, at /srv/pa/wiki.git"
    ]


def test_any_other_host_directory_is_a_problem(home: Path):
    devices = the_store(home)
    devices["pa-catalog"] = disk(data_root(home) / "catalog", "/srv/pa/catalog", True)
    devices["pa-everything"] = disk(data_root(home), "/srv/pa/all", True)

    assert sorted(problems(home, devices)) == [
        f"{data_root(home)} is shared too, at /srv/pa/all",
        f"{data_root(home) / 'catalog'} is shared too, at /srv/pa/catalog",
    ]


def test_unreadable_vm_devices_fail_the_share_checks_and_the_check_carries_on(stand_ins):
    stand_ins.answer("incus", "query", exit=1)

    result = stand_ins.run(Path(LIB).parent / "check-store.sh")

    assert result.returncode == 1
    assert "  FAIL  can't read pa-home's devices" in result.stdout
    assert "  FAIL  can't read the VMs' devices" in result.stdout
