"""check-isolation.sh, run whole on stand-ins for the devbox's tools and the VM (`incus`): a
missing precondition is a FAIL or a skip, and the check carries on to the end rather than stopping
at the first command that fails."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
SCRIPT = DEPLOY / "check-isolation.sh"
INBOX = "/srv/pa/inbox/assistant_chat"


def check(stand_ins: Any, *, inbox_mounted: bool = True) -> Any:
    # The devbox: its own address its only route and the web's, no DNS listener, nothing answering
    # a ping, and no Docker; the probes it would control for are mostly skipped.
    for tool in ("ip", "ss", "ping", "getent", "tailscale", "docker"):
        stand_ins.add(tool)
    stand_ins.answer("ip", "route get", stdout="1.1.1.1 via 127.0.0.1 dev lo src 127.0.0.1\n")
    stand_ins.answer("ping", exit=1)
    stand_ins.answer("getent", "ahostsv4", stdout="127.0.0.1 STREAM localhost\n")
    stand_ins.answer("docker", exit=1)
    if not inbox_mounted:
        # The capture user can't put the Edit probe in an inbox that isn't there.
        stand_ins.answer("incus", "exec", f"{INBOX}/.pa-edit-probe.json", "printf", exit=2)
    return stand_ins.run(SCRIPT)


def test_an_unmounted_inbox_fails_the_file_tools_check_and_the_check_carries_on(stand_ins):
    result = check(stand_ins, inbox_mounted=False)

    assert result.returncode == 1
    failed = f"  FAIL  capture can't put the Edit probe in {INBOX}, so the file tools weren't tried"
    assert failed in result.stdout
    assert "no auto memory is left" in result.stdout


def test_an_unmounted_inbox_starts_no_file_tools_session(stand_ins):
    check(stand_ins, inbox_mounted=False)

    assert not [c for c in stand_ins.of("incus") if "acceptEdits" in c.line]


def test_a_mounted_inbox_has_its_file_tools_tried(stand_ins):
    check(stand_ins)

    assert [c for c in stand_ins.of("incus") if "acceptEdits" in c.line]


def test_a_site_the_devbox_cant_resolve_is_skipped_and_the_check_carries_on(stand_ins):
    stand_ins.add("getent")
    stand_ins.answer("getent", "ahostsv4", exit=2)

    result = check(stand_ins)

    assert "  skip  example.com doesn't resolve on the devbox either" in result.stdout
    assert "no auto memory is left" in result.stdout


def test_an_unpinned_api_name_fails_and_the_check_carries_on(stand_ins):
    stand_ins.answer("incus", "exec", "getent hosts", exit=2)

    result = check(stand_ins)

    assert result.returncode == 1
    assert "isn't pinned in the VM at an address in" in result.stdout
    assert "no auto memory is left" in result.stdout
