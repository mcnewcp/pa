"""seed-data-root.sh: a fresh synthetic data root seeded from a frozen corpus, Argus's or the
scale corpus, with the corpus's owner as the instance owner. It seeds only a data root named like
the synthetic one, never the real one (#99). It runs here with HOME in a temporary directory and
a stand-in `uv` that runs Python as uv would and records the `pa` commands."""

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
    calls.write(json.dumps([*command, os.environ.get("PA_DATA_DIR")]) + "\\n")
"""


def seed_data_root(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs the script, with `{home}` in an argument standing for HOME."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(f"#!{sys.executable}\n{STAND_IN_UV}")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(SEED), *(arg.replace("{home}", str(home)) for arg in args)],
        capture_output=True,
        text=True,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CALLS": str(tmp_path / "calls"),
        },
        check=False,
    )


def share(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".local" / "share"


def payloads(*directories: Path) -> set[str]:
    return {
        p.relative_to(directory).as_posix()
        for directory in directories
        for p in directory.rglob("*")
        if p.is_file()
    }


SYNTHETIC = "{home}/.local/share/pa-home-synthetic"


@pytest.mark.parametrize(
    ("args", "corpus"),
    [((), "argus"), (("--corpus", "argus"), "argus"), (("--corpus", "scale"), "scale")],
)
def test_a_synthetic_data_root_is_seeded_from_the_corpus_named_and_ingested(
    tmp_path: Path, args: tuple[str, ...], corpus: str
):
    result = seed_data_root(tmp_path, *args, SYNTHETIC)

    root = share(tmp_path) / "pa-home-synthetic"

    assert result.returncode == 0, result.stderr
    assert payloads(root / "inbox") == payloads(*frozen.CORPORA[corpus].payloads)
    owner = (root / "owner.env").read_text()
    assert "PA_OWNER_NAME='Argus McNevans'" in owner
    assert "PA_TIME_ZONE=America/Chicago" in owner
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert calls == [["pa", "ingest", str(root)]]
    assert not (share(tmp_path) / "pa-home").exists()


def test_a_synthetic_data_root_of_another_name_is_seeded_there(tmp_path: Path):
    result = seed_data_root(tmp_path, f"{SYNTHETIC}-scale")

    root = share(tmp_path) / "pa-home-synthetic-scale"
    assert result.returncode == 0, result.stderr
    assert (root / "owner.env").is_file()
    assert (root / "inbox" / "assistant_chat").is_dir()


@pytest.mark.parametrize(
    "target",
    [
        (),  # the deploy default, the real data root, is never seeded by default
        ("{home}/.local/share/pa-home",),
        ("{home}/.local/share/pa-home/../pa-home",),
        ("{home}/.local/share/pa-home-real",),
        ("{home}/.local/share/my-pa-home-synthetic",),
    ],
)
def test_anything_but_a_synthetic_data_root_is_refused(tmp_path: Path, target: tuple[str, ...]):
    result = seed_data_root(tmp_path, *target)

    assert result.returncode != 0
    assert "seeds only a synthetic data root" in result.stderr or "usage" in result.stderr
    assert not share(tmp_path).exists() or not any(share(tmp_path).iterdir())
    assert not (tmp_path / "calls").exists()


def test_a_synthetic_name_that_leads_to_the_real_data_root_is_refused(tmp_path: Path):
    real = share(tmp_path) / "pa-home"
    real.mkdir(parents=True)
    (share(tmp_path) / "pa-home-synthetic").symlink_to(real)

    result = seed_data_root(tmp_path, SYNTHETIC)

    assert result.returncode != 0
    assert "seeds only a synthetic data root" in result.stderr
    assert list(real.iterdir()) == []


def test_an_unknown_corpus_is_refused(tmp_path: Path):
    result = seed_data_root(tmp_path, "--corpus", "enron", SYNTHETIC)

    assert result.returncode != 0
    assert "argus or scale" in result.stderr
    assert not (share(tmp_path) / "pa-home-synthetic").exists()
