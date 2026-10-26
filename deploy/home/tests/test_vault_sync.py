"""The vault copy on the devbox (#87): Obsidian's headless Sync client keeps it, pull-only, at a
pinned version on Node 22, as a service, outside every VM's shares. These run here on stand-ins:
a stand-in `node` plays the client, and Incus's device lists are written out. The live checks run
on the devbox (check-vault-sync.sh)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
RUNNER = DEPLOY / "vault-sync" / "pa-vault-sync"

# A stand-in for Node running the client: it answers `sync-config --json` with the configuration
# in $STAND_IN_CONFIG (or fails as the client does when nothing is set up there), and records the
# arguments of anything else it's asked to run.
STAND_IN_NODE = """#!/usr/bin/env bash
shift  # the client's cli.js
case $1 in
  sync-config)
    if [[ -z ${STAND_IN_CONFIG:-} ]]; then
      echo "No sync configuration found for $3" >&2
      exit 3
    fi
    printf '%s\\n' "$STAND_IN_CONFIG"
    ;;
  *) printf '%s\\n' "$*" >>"$STAND_IN_LOG" ;;
esac
"""


def installed(tmp_path: Path) -> Path:
    """The runner installed as install-vault-sync.sh lays it out, with the stand-in Node."""
    root = tmp_path / "vault-sync"
    (root / "bin").mkdir(parents=True)
    shutil.copy(RUNNER, root / "bin" / "pa-vault-sync")
    node = root / "node" / "bin" / "node"
    node.parent.mkdir(parents=True)
    node.write_text(STAND_IN_NODE)
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    client = root / "node_modules" / "obsidian-headless" / "cli.js"
    client.parent.mkdir(parents=True)
    client.write_text("")
    return root


def a_config(vault: Path, mode: str | None = "pull-only") -> str:
    """What `ob sync-config --json` prints for a vault set up for sync."""
    config = {
        "vaultId": "0123abcd",
        "vaultName": "Stand-in vault",
        "vaultPath": str(vault),
        "syncMode": mode or "bidirectional",
        "conflictStrategy": "merge",
        "deviceName": "pa-devbox",
    }
    return json.dumps(config, indent=2)


def runner(
    root: Path, *args: str, config: str | None, log: Path
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "STAND_IN_LOG": str(log)}
    if config is not None:
        env["STAND_IN_CONFIG"] = config
    return subprocess.run(
        [str(root / "bin" / "pa-vault-sync"), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "pa-vault"
    path.mkdir()
    return path


def test_the_runner_syncs_a_pull_only_vault_continuously(tmp_path: Path, vault: Path):
    root, log = installed(tmp_path), tmp_path / "ran"

    result = runner(root, "run", str(vault), config=a_config(vault), log=log)

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [f"sync --continuous --path {vault}"]


# The runner's exit status for a configuration it won't sync (EX_CONFIG): the unit doesn't
# restart on it.
REFUSED = 78


@pytest.mark.parametrize("mode", ["bidirectional", "mirror-remote", None])
def test_the_runner_refuses_a_vault_that_isnt_pull_only(
    tmp_path: Path, vault: Path, mode: str | None
):
    root, log = installed(tmp_path), tmp_path / "ran"

    result = runner(root, "run", str(vault), config=a_config(vault, mode), log=log)

    assert result.returncode == REFUSED
    assert f"{vault} syncs {mode or 'bidirectional'}, not pull-only" in result.stderr
    assert not log.exists()


def test_the_runner_refuses_a_vault_with_no_sync_set_up(tmp_path: Path, vault: Path):
    root, log = installed(tmp_path), tmp_path / "ran"

    result = runner(root, "run", str(vault), config=None, log=log)

    assert result.returncode == REFUSED
    assert f"no sync is set up for {vault}" in result.stderr
    assert not log.exists()


def test_the_runner_never_prints_the_remote_vaults_name(tmp_path: Path, vault: Path):
    root, log = installed(tmp_path), tmp_path / "ran"

    for mode in ("pull-only", "bidirectional"):
        result = runner(root, "check", str(vault), config=a_config(vault, mode), log=log)
        assert "Stand-in vault" not in result.stdout + result.stderr


def test_check_says_whether_the_vault_is_pull_only(tmp_path: Path, vault: Path):
    root, log = installed(tmp_path), tmp_path / "ran"

    pull_only = runner(root, "check", str(vault), config=a_config(vault), log=log)
    both_ways = runner(root, "check", str(vault), config=a_config(vault, None), log=log)

    assert pull_only.returncode == 0, pull_only.stderr
    assert pull_only.stdout.strip() == f"{vault} syncs pull-only"
    assert both_ways.returncode == REFUSED
    assert not log.exists()


def lib(script: str, *, stdin: str = "", home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "USER": "owner"},
        check=False,
    )


def lib_value(name: str, home: Path) -> str:
    result = lib(f'echo "${name}"', home=home)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def disk(source: Path | str, path: str, readonly: bool = True) -> dict[str, str]:
    return {"type": "disk", "source": str(source), "path": path, "readonly": str(readonly).lower()}


def instance(name: str, **devices: dict[str, str]) -> dict[str, Any]:
    root = {"type": "disk", "pool": "pa", "path": "/"}
    return {"name": name, "expanded_devices": {"root": root, **devices}}


def vault_problems(home: Path, instances: list[dict[str, Any]]) -> list[str]:
    """What the check says of the VMs as `incus query /1.0/instances?recursion=1` prints them."""
    result = lib("vault_share_problems", stdin=json.dumps(instances), home=home)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def the_vms_as_set_up(home: Path) -> list[dict[str, Any]]:
    """Both VMs with every share the deploy scripts give them: the assistant's store, and a
    librarian run's L0, project and wiki (a replay's wiki is under replays/)."""
    shares = lib('printf "%s\\n" "${PA_SHARES[@]}"', home=home).stdout.splitlines()
    assistant = {}
    for share in shares:
        device, source, path, readonly = share.split("|")
        assistant[device] = disk(source, path, readonly == "true")
    replay_wiki = Path(lib_value("PA_REPLAYS", home)) / "backfill" / "wiki"
    librarian = {
        "lib-l0": disk(lib_value("PA_L0", home), "/srv/pa/l0"),
        "lib-project": disk("/tmp/pa-librarian-run/project", "/srv/pa/librarian"),
        "lib-wiki": disk(replay_wiki, "/srv/pa/wiki", readonly=False),
    }
    return [instance("pa-home", **assistant), instance("pa-librarian", **librarian)]


def test_no_vm_as_set_up_is_shared_the_vault_copy_or_the_clients_keys(home: Path):
    assert vault_problems(home, the_vms_as_set_up(home)) == []


def test_the_vault_copy_lies_outside_the_data_root(home: Path):
    vault = Path(lib_value("PA_VAULT", home))
    data_root = Path(lib_value("PA_DATA_ROOT", home))

    assert vault.is_relative_to(home)
    assert not vault.is_relative_to(data_root)
    assert not data_root.is_relative_to(vault)


def test_a_vm_shared_the_vault_or_anything_in_it_is_a_problem(home: Path):
    vault = Path(lib_value("PA_VAULT", home))
    vms = the_vms_as_set_up(home)
    vms[0]["expanded_devices"]["daily"] = disk(vault / "Daily", "/srv/daily")
    vms.append(instance("pa-other", notes=disk(vault, "/srv/notes")))

    assert vault_problems(home, vms) == [
        f"pa-home is shared {vault} (as {vault / 'Daily'}, at /srv/daily)",
        f"pa-other is shared {vault} (as {vault}, at /srv/notes)",
    ]


def test_a_vm_shared_a_directory_holding_the_vault_is_a_problem(home: Path):
    vault = Path(lib_value("PA_VAULT", home))
    keys = Path(lib_value("PA_VAULT_SYNC_CONFIG", home))
    vms = [instance("pa-home", everything=disk(home, "/mnt/home"))]

    assert vault_problems(home, vms) == [
        f"pa-home is shared {vault} (as {home}, at /mnt/home)",
        f"pa-home is shared {keys} (as {home}, at /mnt/home)",
    ]


def test_the_clients_keys_shared_are_a_problem(home: Path):
    keys = Path(lib_value("PA_VAULT_SYNC_CONFIG", home))
    vms = [instance("pa-librarian", ob=disk(keys / "sync", "/srv/ob"))]

    assert vault_problems(home, vms) == [
        f"pa-librarian is shared {keys} (as {keys / 'sync'}, at /srv/ob)"
    ]


def test_a_neighbour_with_a_longer_name_isnt_the_vault(home: Path):
    vault = Path(lib_value("PA_VAULT", home))
    vms = [instance("pa-home", other=disk(f"{vault}-old", "/srv/old"))]

    assert vault_problems(home, vms) == []


def unit(home: Path) -> dict[str, list[str]]:
    """The rendered unit's settings: each key's values, in order, whatever its section."""
    result = lib("render_vault_sync_unit", home=home)
    assert result.returncode == 0, result.stderr
    settings: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings.setdefault(key, []).append(value)
    return settings


def test_the_unit_runs_the_pinned_client_over_the_vault_copy_as_the_devbox_user(home: Path):
    settings = unit(home)
    runner_path = Path(lib_value("PA_VAULT_SYNC_DIR", home)) / "bin" / "pa-vault-sync"

    assert settings["ExecStart"] == [f"{runner_path} run {lib_value('PA_VAULT', home)}"]
    assert settings["User"] == ["owner"]
    assert settings["WantedBy"] == ["multi-user.target"]


def test_the_unit_runs_continuously_but_stops_on_a_permanent_error(home: Path):
    """Restarts after a dropped connection or a crash, but not when the vault isn't set up
    pull-only (78), the client isn't logged in (2), or has no sync set up (3)."""
    settings = unit(home)

    assert settings["Restart"] == ["always"]
    assert sorted(settings["RestartPreventExitStatus"][0].split()) == ["2", "3", "78"]


def test_the_unit_can_write_only_the_vault_copy_and_the_clients_state(home: Path):
    settings = unit(home)

    assert sorted(settings["ReadWritePaths"][0].split()) == sorted(
        [lib_value("PA_VAULT", home), lib_value("PA_VAULT_SYNC_CONFIG", home)]
    )
    assert settings["ProtectSystem"] == ["strict"]
    assert settings["ProtectHome"] == ["read-only"]
    assert settings["NoNewPrivileges"] == ["yes"]
    assert settings["PrivateTmp"] == ["yes"]


def test_the_unit_keeps_the_note_names_the_client_prints_out_of_the_journal(home: Path):
    """The client prints each note it downloads by name; its errors still reach the journal."""
    settings = unit(home)

    assert settings["StandardOutput"] == ["null"]
    assert settings.get("StandardError", ["journal"]) == ["journal"]


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_accepts_the_unit(tmp_path: Path, home: Path):
    rendered = lib("render_vault_sync_unit", home=home).stdout
    runner_path = Path(lib_value("PA_VAULT_SYNC_DIR", home)) / "bin" / "pa-vault-sync"
    runner_path.parent.mkdir(parents=True)
    shutil.copy(RUNNER, runner_path)
    path = tmp_path / lib_value("PA_VAULT_SYNC_UNIT", home)
    path.write_text(rendered.replace("User=owner", f"User={os.getuid()}"))

    result = subprocess.run(
        ["systemd-analyze", "verify", str(path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert path.name not in result.stderr  # no warnings about it either


PACKAGE = DEPLOY / "vault-sync"


def test_the_client_is_pinned_exactly_in_the_package_and_its_lockfile(home: Path):
    pinned = lib_value("PA_VAULT_SYNC_VERSION", home)
    package = json.loads((PACKAGE / "package.json").read_text())
    lock = json.loads((PACKAGE / "package-lock.json").read_text())

    assert package["dependencies"] == {"obsidian-headless": pinned}
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"]["node_modules/obsidian-headless"]["version"] == pinned


def test_every_package_the_client_needs_is_locked_with_its_checksum():
    lock = json.loads((PACKAGE / "package-lock.json").read_text())
    installed_packages = {name: entry for name, entry in lock["packages"].items() if name}

    assert installed_packages
    for name, entry in installed_packages.items():
        assert entry["resolved"].startswith("https://registry.npmjs.org/"), name
        assert entry["integrity"].startswith("sha512-"), name


def test_node_is_pinned_to_a_22_release_the_client_accepts(home: Path):
    version = lib_value("PA_NODE_VERSION", home)
    lock = json.loads((PACKAGE / "package-lock.json").read_text())
    engines = lock["packages"]["node_modules/obsidian-headless"]["engines"]["node"]

    assert version.split(".")[0] == "22"
    assert engines == ">=22.0.0"
    assert len(lib_value("PA_NODE_SHA256", home)) == 64


@pytest.mark.skipif(os.uname().machine != "x86_64", reason="the pinned Node is x64's")
def test_a_node_download_that_isnt_the_pinned_release_is_refused(home: Path):
    """A tampered or wrong tarball in the download cache is deleted, and nothing is installed."""
    cache = Path(lib_value("PA_NODE_CACHE", home))
    tarball = cache / f"node-v{lib_value('PA_NODE_VERSION', home)}-linux-x64.tar.xz"
    tarball.parent.mkdir(parents=True)
    tarball.write_bytes(b"not node")

    result = subprocess.run(
        [str(DEPLOY / "install-vault-sync.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "USER": "owner"},
        check=False,
    )

    assert result.returncode != 0
    assert "checksum doesn't match PA_NODE_SHA256: not installed" in result.stderr
    assert not tarball.exists()
    assert not (Path(lib_value("PA_VAULT_SYNC_DIR", home)) / "node").exists()
