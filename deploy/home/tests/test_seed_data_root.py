"""seed-data-root.sh: a fresh data root seeded from a frozen corpus, Argus's or the scale
corpus, with the corpus's owner as the instance owner. It runs here with HOME in a temporary
directory and a stand-in `uv` that runs Python as uv would and records the `pa` commands."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pa_evals import frozen

DEPLOY = Path(__file__).resolve().parents[1]
SEED = DEPLOY / "seed-data-root.sh"

STAND_IN_UV = f"""\
import json, os, sys
argv = sys.argv[1:]
command = argv[argv.index("--project") + 2:]
if command[0] == "python":
    os.execv({sys.executable!r}, [{sys.executable!r}, *command[1:]])
with open(os.environ["CALLS"], "a") as calls:
    calls.write(json.dumps(command) + "\\n")
"""


def seed_data_root(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Runs the script; returns how it ended and the data root it seeds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(f"#!{sys.executable}\n{STAND_IN_UV}")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", str(SEED), *args],
        capture_output=True,
        text=True,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(tmp_path / "calls"),
        },
        check=False,
    )
    return result, home / ".local" / "share" / "pa-home-synthetic"


def payloads(*directories: Path) -> set[str]:
    return {
        p.relative_to(directory).as_posix()
        for directory in directories
        for p in directory.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    ("args", "corpus"),
    [((), "argus"), (("--corpus", "argus"), "argus"), (("--corpus", "scale"), "scale")],
)
def test_a_data_root_is_seeded_from_the_corpus_named_and_ingested(
    tmp_path: Path, args: tuple[str, ...], corpus: str
):
    result, root = seed_data_root(tmp_path, *args)

    assert result.returncode == 0, result.stderr
    assert payloads(root / "inbox") == payloads(*frozen.CORPORA[corpus].payloads)
    owner = (root / "owner.env").read_text()
    assert "PA_OWNER_NAME='Argus McNevans'" in owner
    assert "PA_TIME_ZONE=America/Chicago" in owner
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls == [["pa", "ingest"]]


def test_an_unknown_corpus_is_refused(tmp_path: Path):
    result, root = seed_data_root(tmp_path, "--corpus", "enron")

    assert result.returncode != 0
    assert "argus or scale" in result.stderr
    assert not root.exists()
