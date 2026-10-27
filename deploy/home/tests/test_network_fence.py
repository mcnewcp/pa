"""The assistant VM's network fence (#91): its bridge has no DNS and its NIC an egress ACL that lets
out only TCP 443 to the Anthropic API's range; the devbox pins the API's two hostnames into the
VM, after checking their addresses are in that range; the VM is built on an open bridge, before
any share or credential, and fenced before the store is mounted. The scripts run here on
stand-ins (conftest.py); the isolation check checks the VM itself."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
LIB = DEPLOY / "lib.sh"

API = "160.79.104.10"
OLD_HOSTS = (
    "127.0.0.1 localhost\n"
    "160.79.104.99 api.anthropic.com\n"
    "10.1.2.3 platform.claude.com\n"
    "127.0.1.1 pa-home\n"
)


def lib(script: str, *, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        check=False,
    )


def resolves(stand_ins: Any, host: str, *addresses: str) -> None:
    stand_ins.answer(
        "getent", "ahostsv4", host, stdout="".join(f"{a} STREAM {host}\n" for a in addresses)
    )


def pins(stand_ins: Any) -> subprocess.CompletedProcess[str]:
    stand_ins.add("getent")
    stand_ins.answer("incus", "exec", "pa-home", "cat", "/etc/hosts", stdout=OLD_HOSTS)
    return stand_ins.run(DEPLOY / "pin-hosts.sh")


def test_the_api_hosts_are_pinned_in_the_vm_at_addresses_in_the_apis_range(stand_ins):
    resolves(stand_ins, "api.anthropic.com", "203.0.113.5", API)
    resolves(stand_ins, "platform.claude.com", API)

    result = pins(stand_ins)

    assert result.returncode == 0, result.stderr
    [push] = [c for c in stand_ins.of("incus") if c.argv[:2] == ["file", "push"]]
    assert push.argv[-1] == "pa-home/etc/hosts"
    assert push.argv[2:-2] == ["--quiet", "--uid", "0", "--gid", "0", "--mode", "0644"]
    assert push.pushed == (
        "127.0.0.1 localhost\n"
        "127.0.1.1 pa-home\n"
        f"{API} api.anthropic.com\n"
        f"{API} platform.claude.com\n"
    )


def test_a_host_with_no_address_in_the_range_stops_the_pin_before_the_vm_is_touched(stand_ins):
    resolves(stand_ins, "api.anthropic.com", API)
    resolves(stand_ins, "platform.claude.com", "203.0.113.5")

    result = pins(stand_ins)

    assert result.returncode != 0
    assert "platform.claude.com doesn't resolve to an address in 160.79.104.0/23" in result.stderr
    assert stand_ins.of("incus") == []


PROFILE = DEPLOY / "profile.yaml"


def profile() -> dict[str, Any]:
    return yaml.safe_load(PROFILE.read_text())


def instance(**devices: dict[str, str]) -> dict[str, Any]:
    """The VM as `incus query /1.0/instances/pa-home` prints it."""
    expanded = {"root": {"type": "disk", "path": "/", "pool": "pa"}, **devices}
    return {"name": "pa-home", "expanded_devices": expanded}


def fence_problems(vm: dict[str, Any]) -> list[str]:
    result = lib("vm_fence_problems", stdin=json.dumps(vm))
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


BUILD_NIC = {"type": "nic", "name": "eth0", "network": "pabuild0"}


def test_the_profiles_nic_is_fenced():
    assert fence_problems(instance(eth0=profile()["devices"]["eth0"])) == []


def test_the_vm_on_its_build_bridge_isnt_fenced():
    assert fence_problems(instance(eth0=BUILD_NIC)) == [
        "its NIC eth0 is on pabuild0, not pabr0",
        "its NIC eth0 lacks the egress ACL pa-home-egress",
        "its NIC eth0 doesn't drop what its ACL doesn't let out",
        "its NIC eth0 doesn't drop what its ACL doesn't let in",
    ]


def test_a_second_nic_or_a_widened_acl_isnt_fenced():
    fenced = profile()["devices"]["eth0"]
    other = {**fenced, "network": "incusbr0"}
    opened = {**fenced, "security.acls.default.egress.action": "allow"}

    assert fence_problems(instance(eth0=fenced, eth1=other)) == [
        "its NIC eth1 is on incusbr0, not pabr0"
    ]
    assert fence_problems(instance(eth0=opened)) == [
        "its NIC eth0 doesn't drop what its ACL doesn't let out"
    ]
    assert fence_problems(instance()) == ["it has no NIC"]


def test_the_store_isnt_mounted_into_a_vm_that_isnt_fenced(stand_ins):
    (stand_ins.home / ".local" / "share" / "pa-home-synthetic" / "l0").mkdir(parents=True)
    stand_ins.answer("incus", "query", stdout=json.dumps(instance(eth0=BUILD_NIC)))

    result = stand_ins.run(DEPLOY / "mount-store.sh")

    assert result.returncode != 0
    assert "pa-home isn't fenced" in result.stderr
    assert "fence-vm.sh" in result.stderr
    assert not [c for c in stand_ins.of("incus") if c.argv[:3] == ["config", "device", "add"]]


def mount_store(stand_ins: Any, *, capture: int, assistant: int) -> Any:
    """mount-store.sh into the fenced VM, its users at these uids."""
    (stand_ins.home / ".local" / "share" / "pa-home-synthetic" / "l0").mkdir(parents=True)
    fenced = instance(eth0=profile()["devices"]["eth0"])
    stand_ins.answer("incus", "query", stdout=json.dumps(fenced))
    stand_ins.answer("incus", "id -u capture", stdout=f"{capture}\n")
    stand_ins.answer("incus", "id -u assistant", stdout=f"{assistant}\n")
    stand_ins.answer("incus", "config", "device", "get", exit=1)
    return stand_ins.run(DEPLOY / "mount-store.sh")


def test_the_store_is_mounted_once_the_capture_user_has_the_devbox_users_uid(stand_ins):
    result = mount_store(stand_ins, capture=os.getuid(), assistant=os.getuid() + 1)

    assert result.returncode == 0, result.stderr
    added = [c for c in stand_ins.of("incus") if c.argv[:3] == ["config", "device", "add"]]
    assert len(added) == 4


@pytest.mark.parametrize(
    ("capture", "assistant", "reason"),
    [(1, 0, "capture is uid"), (0, 0, "assistant is uid")],
    ids=["capture-elsewhere", "assistant-still-on-it"],
)
def test_the_store_isnt_mounted_where_the_assistant_could_write_the_inbox(
    stand_ins, capture: int, assistant: int, reason: str
):
    result = mount_store(
        stand_ins, capture=os.getuid() + capture, assistant=os.getuid() + assistant
    )

    assert result.returncode != 0
    assert reason in result.stderr
    assert not [c for c in stand_ins.of("incus") if c.argv[:3] == ["config", "device", "add"]]


def test_the_vm_is_built_without_tailscale_and_leaves_its_hosts_to_the_pins():
    cloud = yaml.safe_load(profile()["config"]["cloud-init.user-data"])

    assert "tailscale" not in PROFILE.read_text().lower().replace("no tailscale", "")
    assert "runcmd" not in cloud
    assert cloud["manage_etc_hosts"] is False
    assert {"git", "bubblewrap", "socat", "apparmor"} <= set(cloud["packages"])


def test_the_vm_is_built_with_the_capture_user_first_and_the_assistant_on_a_uid_of_its_own():
    """The capture user has the devbox user's uid, 1000, so the inbox share is its (#94)."""
    cloud = yaml.safe_load(profile()["config"]["cloud-init.user-data"])
    [assistant_uid] = lib('echo "$PA_VM_ASSISTANT_UID"').stdout.split()

    capture, assistant = cloud["users"]
    assert (capture["name"], capture["uid"]) == ("capture", 1000)
    assert capture["shell"] == "/usr/sbin/nologin"
    assert capture["no_create_home"] is True
    assert (assistant["name"], assistant["uid"]) == ("assistant", int(assistant_uid))
    assert "sudo" not in assistant
    assert "sudo" in cloud["packages"]


def test_the_vm_is_checked_for_the_capture_users_uid_once_built(stand_ins):
    stand_ins.answer("incus", "info", "pa-home", exit=1)
    stand_ins.answer("incus", "id -u capture", stdout="1005\n")

    result = stand_ins.run(DEPLOY / "create-vm.sh")

    assert result.returncode != 0
    assert "capture is uid 1005" in result.stderr


def acl_rules(path: Path) -> list[dict[str, str]]:
    return json.loads(path.read_text())["egress"]


def test_the_assistants_egress_acl_lets_out_only_tcp_443_to_the_apis_range():
    [api_range] = lib('echo "$PA_API_RANGE"').stdout.split()
    [rule] = acl_rules(DEPLOY / "egress-acl.json")

    assert {k: rule[k] for k in ("action", "protocol", "destination", "destination_port")} == {
        "action": "allow",
        "protocol": "tcp",
        "destination": api_range,
        "destination_port": "443",
    }
    assert json.loads((DEPLOY / "egress-acl.json").read_text())["ingress"] == []
    assert acl_rules(DEPLOY / "librarian" / "egress-acl.json") == [rule]


def created(stand_ins: Any, *, exists: bool = False) -> list[Any]:
    stand_ins.answer("incus", "info", "pa-home", exit=0 if exists else 1)
    stand_ins.answer("incus", "id -u capture", stdout=f"{os.getuid()}\n")
    result = stand_ins.run(DEPLOY / "create-vm.sh")
    assert result.returncode == 0, result.stderr
    return stand_ins.calls


def test_the_vm_is_created_on_the_open_build_bridge_and_left_unfenced(stand_ins):
    calls = created(stand_ins)

    init = stand_ins.index(lambda c: c.argv[:1] == ["init"])
    nic = stand_ins.index(lambda c: c.argv[:3] == ["config", "device", "add"])
    start = stand_ins.index(lambda c: c.argv[:2] == ["start", "pa-home"])
    assert init < nic < start
    assert calls[init].argv[1:] == [
        "images:ubuntu/26.04/cloud", "pa-home", "--vm", "--profile", "pa"
    ]  # fmt: skip
    assert calls[nic].argv[3:] == ["pa-home", "eth0", "nic", "network=pabuild0", "name=eth0"]
    assert not [c for c in calls if "tailscale" in c.line]


def set_up(stand_ins: Any, *args: str, acl: dict[str, Any] | None = None) -> list[Any]:
    """incus-setup.sh with no bridge yet, and the ACL as Incus reports it (empty if new)."""
    stand_ins.answer("incus", "network", "show", exit=1)
    reported = acl or {"name": "x", "description": "", "egress": [], "ingress": []}
    stand_ins.answer("incus", "query", "/1.0/network-acls/", stdout=json.dumps(reported))
    result = stand_ins.run(DEPLOY / "incus-setup.sh", *args)
    assert result.returncode == 0, result.stderr
    return stand_ins.calls


def test_the_assistants_bridge_has_no_dns_and_its_acl_is_the_repos(stand_ins):
    lines = [c.line for c in set_up(stand_ins)]

    assert "incus network set pabr0 raw.dnsmasq=port=0" in lines
    assert "incus network acl create pa-home-egress" in lines
    assert "incus network acl edit pa-home-egress" in lines


def test_the_build_bridge_is_made_open_with_dns_and_no_ipv6(stand_ins):
    lines = [c.line for c in set_up(stand_ins)]

    assert (
        "incus network create pabuild0 ipv4.address=auto ipv4.nat=true ipv6.address=none" in lines
    )
    assert not [line for line in lines if "pabuild0" in line and "raw.dnsmasq" in line]
    assert not [line for line in lines if "pabuild0" in line and "acl" in line]


def test_an_acl_that_matches_the_repos_isnt_edited(stand_ins):
    acl = json.loads((DEPLOY / "egress-acl.json").read_text())

    lines = [c.line for c in set_up(stand_ins, acl=acl)]

    assert "incus network acl edit pa-home-egress" not in lines


def test_the_librarians_bridge_and_acl_are_set_up_as_before(stand_ins):
    lines = [c.line for c in set_up(stand_ins, "--vm", "librarian")]

    assert "incus network set palib0 raw.dnsmasq=port=0" in lines
    assert "incus network acl edit pa-librarian-egress" in lines
    assert not [line for line in lines if "pabuild0" in line]


NFT = DEPLOY / "firewall" / "pa-vm.nft"


def chains() -> dict[str, list[str]]:
    """Each chain of the bridge firewall, by name: its rules, comments dropped."""
    found: dict[str, list[str]] = {}
    for name, body in re.findall(r"chain (\w+) \{\n(.*?)\n\t\}", NFT.read_text(), re.DOTALL):
        rules = [line.split("#")[0].strip() for line in body.splitlines()]
        found[name] = [rule for rule in rules if rule]
    return found


def jumps(chain: str, bridge: str) -> list[str]:
    """The chains `chain` jumps to for what comes in on `bridge`."""
    targets = []
    for rule in chains()[chain]:
        match = re.fullmatch(r"iifname (\{[^}]*\}|\"\w+\") jump (\w+)", rule)
        if match and f'"{bridge}"' in match.group(1):
            targets.append(match.group(2))
    return targets


@pytest.mark.parametrize("bridge", ["pabr0", "palib0"])
def test_each_fenced_bridge_reaches_only_dhcp_on_the_devbox_and_tcp_443_in_the_apis_range(
    bridge: str,
):
    [to_devbox] = jumps("input", bridge)
    [out] = jumps("forward", bridge)

    assert chains()[to_devbox] == [
        "ct state established,related accept",
        'udp dport 67 accept comment "DHCP"',
        "counter drop",
    ]
    assert chains()[out] == [
        "meta nfproto ipv6 counter drop",
        "ip daddr != @anthropic_api4 counter drop",
        "meta l4proto != tcp counter drop",
        "tcp dport != 443 counter drop",
    ]


def test_the_build_bridge_reaches_dns_and_dhcp_on_the_devbox_and_nothing_of_the_owners():
    [to_devbox] = jumps("input", "pabuild0")
    [out] = jumps("forward", "pabuild0")

    assert 'udp dport 53 accept comment "DNS"' in chains()[to_devbox]
    assert chains()[to_devbox][-1] == "counter drop"
    assert chains()[out] == ["meta nfproto ipv6 counter drop", "ip daddr @private4 counter drop"]


def test_the_firewalls_api_range_is_lib_shs():
    [api_range] = lib('echo "$PA_API_RANGE"').stdout.split()

    assert f"elements = {{ {api_range} }}" in NFT.read_text()


def firewall_installed(stand_ins: Any) -> list[str]:
    stand_ins.answer("sudo", "ufw", "status", stdout="Status: active\n")
    result = stand_ins.run(DEPLOY / "install-firewall.sh")
    assert result.returncode == 0, result.stderr
    return [c.line for c in stand_ins.of("sudo") if c.argv[:1] == ["ufw"]]


def test_ufw_lets_the_assistants_vm_out_only_to_the_apis_range(stand_ins):
    ufw = firewall_installed(stand_ins)

    assert "sudo ufw route delete allow in on pabr0" in ufw
    assert "sudo ufw delete allow in on pabr0 to any port 53" in ufw
    pabr0 = [line for line in ufw if " on pabr0 " in line and " delete " not in line]
    assert pabr0 == [
        "sudo ufw allow in on pabr0 to any port 67 proto udp comment PA VM DHCP",
        "sudo ufw route allow in on pabr0 to 160.79.104.0/23 port 443 proto tcp"
        " comment PA VM to the Anthropic API",
    ]


def test_ufw_lets_the_build_bridge_have_dns_and_go_out(stand_ins):
    ufw = firewall_installed(stand_ins)

    assert [line for line in ufw if " on pabuild0 " in line] == [
        "sudo ufw allow in on pabuild0 to any port 67 proto udp comment PA VM build DHCP",
        "sudo ufw allow in on pabuild0 to any port 53 comment PA VM build DNS",
        "sudo ufw route allow in on pabuild0 comment PA VM build out",
    ]


def test_an_existing_vm_is_left_alone(stand_ins):
    calls = created(stand_ins, exists=True)

    assert [c.argv[:2] for c in calls if c.prog == "incus"] == [
        ["profile", "show"],
        ["profile", "edit"],
        ["info", "pa-home"],
    ]
