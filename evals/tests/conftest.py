"""Shared by the diagnosis tests (`test_diagnosis.py`, `test_release.py`), since test modules
can't import each other (`--import-mode=importlib`): a synthetic cut of the fixture corpus, what
a scripted diagnostician leaves, and a `pa-eval` runner that holds every command to printing
nothing a session wrote.

Everything a session "wrote" here is made up, and each piece holds one of the words in `SALT`.
A command prints names, paths, and counts, so none of those words may ever reach its output.
"""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import pytest

from pa_core.catalog import open_catalog
from pa_core.envelope import Source
from pa_core.ingest import ingest
from pa_core.owner import Owner
from pa_core.status import SourceStatus, Status, write_status
from pa_evals.cli import main
from pa_evals.eval_set import load_eval_set
from pa_home.config import DataRoot
from pa_home.cut import Cut, take_cut
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

_FIXTURES = Path(__file__).parent / "fixtures"
_OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))

_REFERRAL = """\
# Why do two questions fail more often on the dreamed wiki?

Questions `r07` and `r12` pass on the cut's own wiki and fail on the dreamed copy. Say which
dreaming change is behind each, and whether the change itself was right.
"""
_ANALYSIS = """\
# Private analysis

Zebulon Quackenbush asked about the marmalade invoice twice, and the dreamed wiki files the
second ask under his brother's page.
"""
_REPLY = "I left the analysis and the findings. Zebulon's marmalade invoice was behind both."

SALT = (
    *("zebulon", "quackenbush", "marmalade", "wombat", "gooseberry"),
    # What stands in for real content in a case (`test_release_real_content.py`).
    *("ottoline", "varga", "temperance", "oyelaran", "cornelius", "abernathy", "wilhelmina"),
    *("stroud", "billie", "okafor", "quince", "collective", "mcnevans", "heron", "sgraffito"),
    *("fennimore", "quillfeather", "cuthbert", "plumtree"),
)
"""A word from everything a session wrote in these tests, and from everything that stands in
for real content in a case. `gooseberry` is for what a test plants itself: a file's name, a
field's name, a path. No test's name holds one of them: a test's name is in its temporary
directory's path, which a command prints."""

RULES = ("shape", "schema", "paths", "episodes", "names", "addresses", "quotes", "digits")
"""Every rule of the release check, by the name its count is printed and recorded under."""

NAME = "dreamed-drop"
"""The diagnosis a `desk` makes and releases."""
REFUSED = 3
"""The exit code of a refusal, which a script tells from a failure (1)."""


@dataclass(frozen=True)
class AlsoInTheCase:
    """What a test module's case holds beyond the fixture corpus and its wiki."""

    payloads: dict[str, str] = field(default_factory=dict)
    """Raw payloads, by inbox path (`gmail/kiln.eml`)."""
    wiki: dict[str, str] = field(default_factory=dict)
    """Wiki files, by path in the wiki (`people/wilhelmina-stroud.md`)."""


@pytest.fixture
def also_in_the_case() -> AlsoInTheCase:
    """Nothing, unless a test module says otherwise with a fixture of this name."""
    return AlsoInTheCase()


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


@pytest.fixture
def cut(data_root: DataRoot, also_in_the_case: AlsoInTheCase) -> Cut:
    """A cut of a data root holding the fixture corpus, with a wiki and a status file, and
    whatever else the test module's case holds."""
    as_of = load_eval_set(_FIXTURES / "eval_set.json").as_of
    shutil.copytree(_FIXTURES / "corpus", data_root.inbox)
    for name, payload in also_in_the_case.payloads.items():
        (data_root.inbox / name).write_text(payload)
    store = FilesystemL0(data_root.l0)
    captured = as_of - timedelta(days=1)
    with open_catalog(data_root.catalog, store) as catalog:
        ingest(data_root.inbox, store, catalog, _OWNER, now=lambda: captured)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    shutil.copytree(_FIXTURES / "eval-wiki" / "wiki", data_root.wiki, dirs_exist_ok=True)
    for name, text in also_in_the_case.wiki.items():
        (data_root.wiki / name).parent.mkdir(exist_ok=True)
        (data_root.wiki / name).write_text(text)
    repository.commit("Observe a batch\n\nLibrarian-Version: fedcba987654\n")
    status = Status(
        sources={Source.GMAIL: SourceStatus(watermark=captured, cadence=timedelta(hours=1))}
    )
    write_status(data_root.status, status, at=captured)
    return take_cut(data_root, now=as_of.astimezone(UTC))


@pytest.fixture
def referral(tmp_path: Path) -> Path:
    """A referral: a development session's question, which holds nothing real."""
    path = tmp_path / "referral.md"
    path.write_text(_REFERRAL)
    return path


@pytest.fixture
def reply(tmp_path: Path) -> Path:
    """What a scripted diagnostician ends its session by saying."""
    path = tmp_path / "reply.txt"
    path.write_text(_REPLY)
    return path


@pytest.fixture
def well_formed() -> dict[str, Any]:
    """Findings that fit the schema and blame paths that are in the repository."""
    return {
        "answered": "partly",
        "summary": "The dreamed wiki merged two people, so the marmalade answers went astray.",
        "findings": [
            {
                "id": "f1",
                "kind": "fault",
                "component": "dreaming",
                "questions": ["r07", "r12"],
                "episodes": ["gmail_94f6b4cbc55a3b85", "obsidian_0510599eb323eee0"],
                "runs": {"own": [0, 3], "dreamed": [2, 6]},
                "mechanism": "A merge keeps the older page's summary, which loses the wombat.",
                "code": ["librarian/CLAUDE.md", "core/src/pa_core/librarian/dreaming.py"],
                "suggestion": "Have the host refuse a merge whose pages disagree on a wombat.",
                "confidence": "medium",
            },
            {
                "id": "f2",
                "kind": "noise",
                "component": "judge",
                "questions": ["r12"],
                "episodes": [],
                "runs": {"dreamed": [1, 6]},
                "mechanism": "The judge marks one marmalade answer down in one run of six.",
                "code": [],
                "suggestion": "",
                "confidence": "low",
            },
        ],
    }


@pytest.fixture
def leavings(tmp_path: Path, well_formed: dict[str, Any]) -> Callable[..., Path]:
    """Makes a directory holding what a scripted diagnostician leaves: by default a private
    analysis and well-formed findings. `findings` replaces the findings (a `str` is written as
    it is), and `extra` adds files by name."""
    made = iter(range(1000))

    def leave(findings: Any = None, *, extra: dict[str, str] | None = None) -> Path:
        directory = tmp_path / f"leavings-{next(made)}"
        directory.mkdir()
        (directory / "analysis.md").write_text(_ANALYSIS)
        left = well_formed if findings is None else findings
        (directory / "findings.json").write_text(
            left if isinstance(left, str) else json.dumps(left, indent=2)
        )
        for name, text in (extra or {}).items():
            (directory / name).write_text(text)
        return directory

    return leave


@dataclass(frozen=True)
class Ran:
    """What a `pa-eval` command did, as its caller sees it."""

    code: int
    out: str
    err: str


@pytest.fixture
def pa_eval(capsys: pytest.CaptureFixture[str]) -> Callable[..., Ran]:
    """Runs `pa-eval` with the given arguments, and asserts that nothing it printed holds a
    word a session wrote. Every diagnosis test runs its commands through here."""

    def run(*arguments: object) -> Ran:
        code = main([str(argument) for argument in arguments])
        captured = capsys.readouterr()
        for word in SALT:
            assert word not in captured.out.lower()
            assert word not in captured.err.lower()
        return Ran(code, captured.out, captured.err)

    return run


@dataclass(frozen=True)
class Desk:
    """Where a release happens in a test: the private diagnoses directory under the data root,
    and the released directory outside it."""

    private: Path
    released: Path
    release: Callable[..., Ran]
    """Runs `pa-eval release` for the diagnosis named `NAME`."""
    diagnose: Callable[..., Path]
    """Makes the diagnosis named `NAME`, whose session leaves the given directory's files."""

    def refused(self, ran: Ran, **problems: int) -> str:
        """Asserts that `ran` was a refusal that found `problems` (a count per rule; 0 for the
        rest), released nothing, printed nothing but names and counts, and kept its notes for
        the owner alone. Returns the notes."""
        assert ran.code == REFUSED
        assert not self.released.exists()
        assert not self.released.parent.exists() or not list(self.released.parent.iterdir())
        counts = {**dict.fromkeys(RULES, 0), **problems}
        notes = self.private / "check-notes.md"
        assert ran.out.splitlines() == [
            f"release: {NAME}: refused, and nothing is released",
            *(f"check {rule}: {count}" for rule, count in counts.items()),
            f"notes: {notes}",
        ]
        assert ran.err == ""
        assert stat.S_IMODE(notes.stat().st_mode) == 0o600
        assert stat.S_IMODE(self.private.stat().st_mode) == 0o700
        text = notes.read_text()
        for rule, count in counts.items():
            assert f"## {rule}: {count}\n" in text
        return text


@pytest.fixture
def desk(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    referral: Path,
    pa_eval: Callable[..., Ran],
    monkeypatch: pytest.MonkeyPatch,
) -> Desk:
    """A release's desk: the owner is the corpus's fictional one, as the cut was taken for."""
    released = tmp_path / "released"
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    monkeypatch.setenv("PA_RELEASED_DIR", str(released))
    monkeypatch.setenv("PA_OWNER_NAME", _OWNER.name)
    monkeypatch.setenv("PA_OWNER_EMAILS", ",".join(_OWNER.email_addresses))
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", ",".join(_OWNER.other_names))

    def diagnose(leaves: Path) -> Path:
        ran = pa_eval(
            *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
            *("--out", data_root.diagnoses, "--agent", "scripted", "--leaves", leaves),
        )
        assert ran.code == 0
        return data_root.diagnoses / NAME

    def release(*extra: object) -> Ran:
        return pa_eval("release", "--name", NAME, "--cut", cut.path, *extra)

    return Desk(data_root.diagnoses / NAME, released / NAME, release, diagnose)
