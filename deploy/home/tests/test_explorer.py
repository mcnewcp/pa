"""The explorer image's pieces that run without Docker: the build follower, which restarts the
explorer's server on each newly published build, and the image's LadybugDB pin."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
EXPLORER = REPOSITORY / "deploy" / "home" / "explorer"
FOLLOWER = EXPLORER / "follow-current"


class Graph:
    """A graph directory as the librarian lays it out: builds, and `current` pointing at one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def publish(self, build_id: str) -> None:
        (self.path / "builds" / build_id).mkdir(parents=True)
        pointer = self.path / f".current.{build_id}"
        pointer.symlink_to(Path("builds") / build_id)
        pointer.replace(self.path / "current")


class Server:
    """A stand-in for the explorer's server: records the file it was started on, then waits."""

    def __init__(self, directory: Path) -> None:
        self.log = directory / "started"
        self.script = directory / "server"
        self.script.write_text(f'#!/bin/sh\necho "$LBUG_FILE $$" >>{self.log}\nexec sleep 600\n')
        self.script.chmod(0o755)

    def starts(self) -> list[tuple[str, int]]:
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        return [(file, int(pid)) for file, pid in (line.split() for line in lines)]

    def wait_for(self, count: int) -> list[tuple[str, int]]:
        deadline = time.monotonic() + 10
        while len(self.starts()) < count:
            assert time.monotonic() < deadline, f"started {self.starts()}, expected {count}"
            time.sleep(0.05)
        return self.starts()


@pytest.fixture
def graph(tmp_path: Path) -> Graph:
    path = tmp_path / "graph"
    path.mkdir()
    return Graph(path)


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


@pytest.fixture
def follower(graph: Graph, server: Server) -> Iterator[subprocess.Popen[bytes]]:
    environ = {**os.environ, "LBUG_DIR": str(graph.path), "FOLLOW_INTERVAL": "0.1"}
    process = subprocess.Popen([str(FOLLOWER), str(server.script)], env=environ)
    yield process
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    for _, pid in server.starts():
        _kill(pid)


def test_serves_the_current_build_and_restarts_on_a_new_one(
    graph: Graph, server: Server, follower: subprocess.Popen[bytes]
) -> None:
    graph.publish("b1")

    [(file, first)] = server.wait_for(1)
    assert file == "builds/b1/graph.lbdb"

    graph.publish("b2")

    assert server.wait_for(2)[1][0] == "builds/b2/graph.lbdb"
    assert not _alive(first)


def test_exits_with_the_servers_status_when_it_dies(
    graph: Graph, server: Server, follower: subprocess.Popen[bytes]
) -> None:
    graph.publish("b1")
    [(_, pid)] = server.wait_for(1)

    os.kill(pid, signal.SIGKILL)

    assert follower.wait(timeout=10) == 128 + signal.SIGKILL


def test_stops_the_server_when_stopped(
    graph: Graph, server: Server, follower: subprocess.Popen[bytes]
) -> None:
    graph.publish("b1")
    [(_, pid)] = server.wait_for(1)

    follower.terminate()

    assert follower.wait(timeout=10) == 0
    assert not _alive(pid)


def test_the_image_uses_the_ladybugdb_version_the_builder_and_server_use() -> None:
    adapter = tomllib.loads((REPOSITORY / "adapters" / "home" / "pyproject.toml").read_text())
    [pin] = [d for d in adapter["project"]["dependencies"] if d.startswith("ladybug==")]
    version = pin.removeprefix("ladybug==")
    package = json.loads((EXPLORER / "package.json").read_text())
    lock = json.loads((EXPLORER / "package-lock.json").read_text())

    for name in ("@ladybugdb/core", "@ladybugdb/wasm-core"):
        assert package["dependencies"][name] == version
        assert lock["packages"][f"node_modules/{name}"]["version"] == version


def test_the_image_has_no_ai_client() -> None:
    package = json.loads((EXPLORER / "package.json").read_text())

    assert "openai" not in package["dependencies"]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
