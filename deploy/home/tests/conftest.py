"""Stand-ins for the commands the deploy scripts run on the devbox (`incus`, `sudo`, `getent`,
`curl`, ...), for the tests that run a script whole. Each stand-in records how it was called, what
was piped to it, and what a `file push` pushed; it answers by the first rule that matches."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

DEPLOY = Path(__file__).resolve().parents[1]

STAND_IN = """\
import json, os, sys
prog, argv = os.path.basename(sys.argv[0]), sys.argv[1:]
if prog == "sudo" and argv[:1] == ["incus"]:
    os.execvp(os.path.join(os.path.dirname(sys.argv[0]), "incus"), argv)
stdin = ""
if prog == "incus" and argv[:1] == ["exec"] or prog in os.environ.get("READS_STDIN", "").split():
    stdin = sys.stdin.buffer.read().decode(errors="replace")
pushed = None
if prog == "incus" and argv[:2] == ["file", "push"] and os.path.isfile(argv[-2]):
    with open(argv[-2], "rb") as source:
        pushed = source.read(1 << 20).decode(errors="replace")
env = {k: v for k, v in os.environ.items() if k.startswith(("UV_", "GIT_"))}
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps({"prog": prog, "argv": argv, "stdin": stdin, "pushed": pushed,
                            "env": env, "cwd": os.getcwd()}) + "\\n")
joined = " ".join(argv)
used_path = os.environ["RULES"] + ".used"
used = json.load(open(used_path)) if os.path.exists(used_path) else {}
for index, rule in enumerate(json.load(open(os.environ["RULES"]))):
    if rule["prog"] == prog and all(m in joined for m in rule["match"]):
        if rule["times"] is not None:
            if used.get(str(index), 0) >= rule["times"]:
                continue
            used[str(index)] = used.get(str(index), 0) + 1
            with open(used_path, "w") as out:
                json.dump(used, out)
        for path, content in rule.get("writes", {}).items():
            path = path.replace("{last}", argv[-1] if argv else "")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as out:
                out.write(content)
        sys.stdout.write(rule["stdout"])
        sys.exit(rule["exit"])
"""


@dataclass
class Call:
    prog: str
    argv: list[str]
    stdin: str
    pushed: str | None
    env: dict[str, str]
    cwd: str

    @property
    def line(self) -> str:
        return " ".join([self.prog, *self.argv])


class StandIns:
    """A bin directory of stand-ins, the rules they answer by, and the calls they record."""

    def __init__(self, tmp_path: Path, progs: tuple[str, ...]):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.home = tmp_path / "home"
        self.home.mkdir(exist_ok=True)
        self._calls = tmp_path / "calls"
        self._rules_file = tmp_path / "rules.json"
        self._rules: list[dict[str, Any]] = []
        for prog in progs:
            self.add(prog)

    def add(self, prog: str) -> None:
        script = self.bin / prog
        script.write_text(f"#!{sys.executable}\n{STAND_IN}")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def answer(
        self,
        prog: str,
        *match: str,
        stdout: str = "",
        exit: int = 0,
        writes: dict[str, str] | None = None,
        times: int | None = None,
    ) -> None:
        """Calls of `prog` whose arguments hold every `match` print `stdout` and exit `exit`, for
        the first `times` such calls if given. Rules added first win."""
        rule: dict[str, Any] = {"prog": prog, "match": list(match), "stdout": stdout, "exit": exit}
        rule["writes"] = writes or {}
        rule["times"] = times
        self._rules.append(rule)

    def run(
        self, script: str | Path, *args: str, env: dict[str, str] | None = None, stdin: str = ""
    ) -> subprocess.CompletedProcess[str]:
        self._rules_file.write_text(json.dumps(self._rules))
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            input=stdin,
            capture_output=True,
            text=True,
            env={
                "HOME": str(self.home),
                "USER": "owner",
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "CALLS": str(self._calls),
                "RULES": str(self._rules_file),
                **(env or {}),
            },
            check=False,
        )

    @property
    def calls(self) -> list[Call]:
        if not self._calls.exists():
            return []
        return [Call(**json.loads(line)) for line in self._calls.read_text().splitlines()]

    def of(self, prog: str) -> list[Call]:
        return [c for c in self.calls if c.prog == prog]

    def index(self, found: Any) -> int:
        """The index of the one call `found` accepts."""
        [index] = [i for i, call in enumerate(self.calls) if found(call)]
        return index


@pytest.fixture
def stand_ins(tmp_path: Path) -> StandIns:
    return StandIns(tmp_path, ("incus", "sudo"))
