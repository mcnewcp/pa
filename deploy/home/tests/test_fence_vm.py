"""fence-vm.sh (#91): the assistant's VM, built open, is fenced before anything is shared into it
or any credential entered. While it can still reach the internet it gets its sandbox's packages
and gives up Tailscale; then it's moved, stopped, off the build bridge onto its own, the API's
names are pinned, and the devbox renews the pins at each boot. It runs here on stand-ins
(conftest.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"
API = "160.79.104.10"
FENCED_NIC = {
    "type": "nic",
    "name": "eth0",
    "network": "pabr0",
    "security.acls": "pa-home-egress",
    "security.acls.default.egress.action": "drop",
    "security.acls.default.ingress.action": "drop",
}
BUILD_NIC = {"type": "nic", "name": "eth0", "network": "pabuild0"}


def vm(nic: dict[str, str], *, local: bool) -> str:
    devices = {"eth0": nic} if local else {}
    return json.dumps({"name": "pa-home", "expanded_devices": {"eth0": nic}, "devices": devices})


def fence(
    stand_ins: Any,
    *,
    built: bool = True,
    tailscale: bool = True,
    dns: str = "port=0",
    resolves_after: bool = False,
) -> subprocess.CompletedProcess[str]:
    stand_ins.add("getent")
    for host in ("api.anthropic.com", "platform.claude.com"):
        stand_ins.answer("getent", host, stdout=f"{API} STREAM {host}\n")
    stand_ins.answer("incus", "network", "get", "pabr0", "raw.dnsmasq", stdout=f"{dns}\n")
    acl = json.loads((DEPLOY / "egress-acl.json").read_text())
    stand_ins.answer("incus", "query", "/1.0/network-acls/", stdout=json.dumps(acl))
    if built:
        # On the build bridge until its own NIC is removed.
        built_vm = vm(BUILD_NIC, local=True)
        stand_ins.answer("incus", "query", "/1.0/instances/", stdout=built_vm, times=1)
    stand_ins.answer("incus", "query", "/1.0/instances/", stdout=vm(FENCED_NIC, local=False))
    stand_ins.answer("incus", "list", stdout="RUNNING\n")
    stand_ins.answer("incus", "command -v tailscale", exit=0 if tailscale else 1)
    stand_ins.answer("incus", "cat /etc/hosts", stdout="127.0.0.1 localhost\n")
    stand_ins.answer("incus", "getent", "example.com", exit=0 if resolves_after else 2)
    return stand_ins.run(DEPLOY / "fence-vm.sh", env={"READS_STDIN": "sudo"})


def fenced(stand_ins: Any, **kwargs: Any) -> list[Any]:
    result = fence(stand_ins, **kwargs)
    assert result.returncode == 0, result.stderr
    return stand_ins.calls


def test_the_vm_leaves_the_build_bridge_while_stopped_once_it_has_its_packages_and_pins(
    stand_ins,
):
    calls = fenced(stand_ins)

    packages = stand_ins.index(lambda c: "dpkg-query" in c.line)
    pins = [i for i, c in enumerate(calls) if c.argv[-1:] == ["pa-home/etc/hosts"]]
    stop = stand_ins.index(lambda c: c.argv[:2] == ["stop", "pa-home"])
    remove = stand_ins.index(lambda c: c.argv[:3] == ["config", "device", "remove"])
    start = stand_ins.index(lambda c: c.argv[:2] == ["start", "pa-home"])
    assert packages < pins[0] < stop < remove < start < pins[1]
    assert calls[remove].argv[3:] == ["pa-home", "eth0"]


def test_a_vm_whose_nic_is_the_profiles_already_is_restarted_behind_the_fence(stand_ins):
    calls = fenced(stand_ins, built=False)

    assert not [c for c in calls if c.argv[:3] == ["config", "device", "remove"]]
    assert stand_ins.index(lambda c: c.argv[:2] == ["stop", "pa-home"]) < stand_ins.index(
        lambda c: c.argv[:2] == ["start", "pa-home"]
    )


def test_the_fenced_nic_comes_from_the_profile(stand_ins):
    fenced(stand_ins)

    edit = stand_ins.index(lambda c: c.argv[:3] == ["profile", "edit", "pa"])
    assert edit < stand_ins.index(lambda c: c.argv[:2] == ["start", "pa-home"])


def test_tailscale_is_logged_out_and_purged_while_the_vm_can_still_reach_it(stand_ins):
    calls = fenced(stand_ins)

    logout = stand_ins.index(lambda c: "tailscale logout" in c.line)
    purge = stand_ins.index(lambda c: "purge" in c.argv and "tailscale" in c.argv)
    assert logout < purge < stand_ins.index(lambda c: c.argv[:2] == ["stop", "pa-home"])
    assert "/var/lib/tailscale" in calls[purge + 1].line


def test_a_vm_without_tailscale_has_nothing_purged(stand_ins):
    calls = fenced(stand_ins, tailscale=False)

    assert not [c for c in calls if "tailscale logout" in c.line or "purge" in c.argv]


def test_the_devbox_pins_the_hosts_again_at_each_boot(stand_ins):
    calls = fenced(stand_ins)

    [unit] = [c for c in stand_ins.of("sudo") if c.argv[:1] == ["tee"]]
    assert unit.argv[-1] == "/etc/systemd/system/pa-pins@.service"
    assert "User=owner\n" in unit.stdin
    assert f"ExecStart=/usr/bin/env PA_ASSISTANT_VM=%i {DEPLOY}/pin-hosts.sh\n" in unit.stdin
    assert "After=network-online.target incus.service incus-startup.service\n" in unit.stdin
    assert "sudo systemctl enable pa-pins@pa-home.service" in [c.line for c in calls]


def test_a_bridge_with_dns_stops_the_fence_before_the_vm_is_touched(stand_ins):
    result = fence(stand_ins, dns="")

    assert result.returncode != 0
    assert "pabr0 serves DNS: run incus-setup.sh" in result.stderr
    assert not [c for c in stand_ins.of("incus") if "snapshot" in c.argv or "stop" in c.argv]


def test_a_rollback_pins_the_hosts_again_once_the_vm_is_back(stand_ins):
    stand_ins.add("getent")
    for host in ("api.anthropic.com", "platform.claude.com"):
        stand_ins.answer("getent", host, stdout=f"{API} STREAM {host}\n")
    snapshots = ["/1.0/instances/pa-home/snapshots/pre-update-20261026T120000Z"]
    stand_ins.answer("incus", "query", "/snapshots", stdout=json.dumps(snapshots))

    result = stand_ins.run(DEPLOY / "rollback.sh")

    assert result.returncode == 0, result.stderr
    restore = stand_ins.index(lambda c: c.argv[:2] == ["snapshot", "restore"])
    pins = stand_ins.index(lambda c: c.argv[-1:] == ["pa-home/etc/hosts"])
    assert restore < stand_ins.index(lambda c: c.argv[:2] == ["start", "pa-home"]) < pins


def test_a_vm_that_still_resolves_names_once_fenced_fails(stand_ins):
    result = fence(stand_ins, resolves_after=True)

    assert result.returncode != 0
    assert "pa-home still resolves example.com" in result.stderr
