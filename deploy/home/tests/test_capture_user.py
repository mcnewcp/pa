"""capture-user.sh, which install-assistant-fences.sh runs in the assistant's VM as root (#94): the
capture user, with the devbox user's uid, and the helper's state directory, its alone. A VM built
before #94 gave that uid to the assistant, which moves to a uid of its own first. The script runs
here on stand-ins for the user tools, which record each call and answer as the VM would."""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parents[1]
SCRIPT = DEPLOY / "capture-user.sh"
TOOLS = (
    "id",
    "getent",
    "pgrep",
    "findmnt",
    "groupadd",
    "groupmod",
    "useradd",
    "usermod",
    "find",
    "install",
)
ARGS = ("assistant", "capture", "1000", "1001", "/var/lib/pa-capture")


def run(stand_ins: Any) -> Any:
    for tool in TOOLS:
        stand_ins.add(tool)
    # Unless a test says otherwise: nothing runs as the assistant, no uid or gid is taken, and
    # the VM's own disk is ext4.
    stand_ins.answer("pgrep", exit=1)
    stand_ins.answer("getent", exit=2)
    stand_ins.answer("findmnt", "-no FSTYPE /", stdout="ext4\n")
    return stand_ins.run(SCRIPT, *ARGS)


def changes(stand_ins: Any) -> list[str]:
    return [c.line for c in stand_ins.calls if c.prog not in ("id", "getent", "pgrep", "findmnt")]


def test_a_vm_built_with_the_capture_user_only_gets_the_state_directory(stand_ins: Any):
    stand_ins.answer("id", "-u assistant", stdout="1001\n")
    stand_ins.answer("id", "-u capture", stdout="1000\n")

    result = run(stand_ins)

    assert result.returncode == 0, result.stderr
    assert changes(stand_ins) == [
        "install -d -o capture -g capture -m 0700 /var/lib/pa-capture",
    ]


def test_a_vm_from_before_moves_the_assistant_off_the_devbox_users_uid_then_adds_capture(
    stand_ins: Any,
):
    stand_ins.answer("id", "-u assistant", stdout="1000\n")
    stand_ins.answer("id", "-g assistant", stdout="1000\n", times=1)
    stand_ins.answer("id", "-g assistant", stdout="1001\n")
    stand_ins.answer("id", "-u capture", exit=1, times=1)
    stand_ins.answer("id", "-u capture", stdout="1000\n")

    result = run(stand_ins)

    assert result.returncode == 0, result.stderr
    # Its home directory goes with usermod; what it owns elsewhere on the VM's own disk, its
    # scratch and evals' directories, goes with find. The shares are other filesystems, left
    # alone: the inbox's files become the capture user's, as they are on the devbox. find's
    # -xdev still lists each mount point itself, the root of another filesystem, so only files
    # on the VM's own disk's filesystem type are changed (pa-home, 2026-10-27).
    assert changes(stand_ins) == [
        "groupmod -g 1001 assistant",
        "find / -xdev -fstype ext4 -gid 1000 -exec chgrp -h 1001 {} +",
        "usermod -u 1001 assistant",
        "find / -xdev -fstype ext4 -uid 1000 -exec chown -h 1001 {} +",
        "groupadd -g 1000 capture",
        "useradd -u 1000 -g capture -M -d /nonexistent -s /usr/sbin/nologin capture",
        "install -d -o capture -g capture -m 0700 /var/lib/pa-capture",
    ]


def test_a_move_stopped_after_the_group_finishes_on_a_re_run(stand_ins: Any):
    stand_ins.answer("id", "-u assistant", stdout="1000\n")
    stand_ins.answer("id", "-g assistant", stdout="1001\n")
    stand_ins.answer("id", "-u capture", exit=1, times=1)
    stand_ins.answer("id", "-u capture", stdout="1000\n")

    result = run(stand_ins)

    assert result.returncode == 0, result.stderr
    assert changes(stand_ins)[:3] == [
        "find / -xdev -fstype ext4 -gid 1000 -exec chgrp -h 1001 {} +",
        "usermod -u 1001 assistant",
        "find / -xdev -fstype ext4 -uid 1000 -exec chown -h 1001 {} +",
    ]


def test_the_assistant_isnt_moved_while_anything_runs_as_it(stand_ins: Any):
    stand_ins.answer("id", "-u assistant", stdout="1000\n")
    stand_ins.answer("pgrep", "-u assistant", exit=0)

    result = run(stand_ins)

    assert result.returncode != 0
    assert "stop" in result.stderr
    assert changes(stand_ins) == []


def test_a_capture_user_with_another_uid_is_refused(stand_ins: Any):
    stand_ins.answer("id", "-u assistant", stdout="1001\n")
    stand_ins.answer("id", "-u capture", stdout="1002\n")

    result = run(stand_ins)

    assert result.returncode != 0
    assert "1002" in result.stderr
    assert changes(stand_ins) == []
