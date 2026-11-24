"""Shared by the diagnosis tests (`test_diagnosis.py`, `test_release.py`), since test modules
can't import each other (`--import-mode=importlib`): a synthetic cut of the fixture corpus, what
a scripted diagnostician leaves, and a `pa-eval` runner that holds every command to printing
nothing a session wrote.

Everything a session "wrote" here is made up, and each piece holds one of the words in `SALT`.
A command prints names, paths, and counts, so none of those words may ever reach its output.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
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
from pa_evals.results import CutResult, QuestionResult, RunResults, Scores
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
    # And in the parts of a whole case (`test_release_case.py`).
    *("perpetua", "nightingale", "ignatius", "thistlewood", "iggy", "thistledown"),
)
"""A word from everything a session wrote in these tests, and from everything that stands in
for real content in a case. `gooseberry` is for what a test plants itself: a file's name, a
field's name, a path. No test's name holds one of them: a test's name is in its temporary
directory's path, which a command prints."""

RULES = (
    *("shape", "schema", "paths", "episodes", "questions", "labels"),
    *("names", "addresses", "quotes", "digits"),
    *("reproductions", "one-word-names", "owner", "ingest", "lint"),
)
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
                "reproduction": None,
                "no_reproduction": "needs-scale",
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
                "reproduction": None,
                "no_reproduction": "variance",
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


QUESTIONS = {
    "r07": ("Which invoice did the grocer send twice?", "The one for the preserves."),
    "r12": ("Who is the second invoice filed under?", "The grocer's brother."),
}
"""The questions of the case's eval set, by opaque id: each with its expected answer."""
EVIDENCE = "gmail_94f6b4cbc55a3b85"
"""An episode of the fixture corpus, which every question here names as its evidence."""


@pytest.fixture
def write_eval_set(tmp_path: Path, cut: Cut) -> Callable[..., Path]:
    """Writes an eval set asked over the cut, of the given questions (each an id with its
    question and expected answer; by default `QUESTIONS`), and returns its file."""

    def write(questions: dict[str, tuple[str, str]] | None = None) -> Path:
        path = tmp_path / "evals" / "eval-set.json"
        path.parent.mkdir(exist_ok=True)
        eval_set = {
            "as_of": cut.as_of.isoformat(),
            "owner": {"name": _OWNER.name, "email_addresses": list(_OWNER.email_addresses)},
            "time_zone": "America/Chicago",
            "questions": [
                {
                    "id": question_id,
                    "category": "single_fact_recall",
                    "question": question,
                    "expected_answer": expected,
                    "evidence": [EVIDENCE],
                }
                for question_id, (question, expected) in (questions or QUESTIONS).items()
            ],
        }
        path.write_text(json.dumps(eval_set, indent=2))
        return path

    return write


@pytest.fixture
def lay_runs(tmp_path: Path, cut: Cut) -> Callable[..., Path]:
    """Makes a run directory of `count` runs over the cut, laid as `pa-eval run --runs N` lays
    them, and returns it. Each run answers every question of `QUESTIONS` the same way."""

    def lay(
        name: str,
        count: int,
        *,
        answer: str = "The one for the jam.",
        reasoning: str = "Close, and still the wrong jar.",
        over: str | None = None,
    ) -> Path:
        asked = CutResult(
            name=over or cut.name, as_of=cut.as_of, episodes=cut.manifest.episodes, wiki_commit=None
        )
        questions = [
            QuestionResult(
                id=question_id,
                category="single_fact_recall",
                question=question,
                as_of=cut.as_of,
                expected_answer=expected,
                evidence=[EVIDENCE],
                status="scored",
                answer=answer,
                cited=[EVIDENCE],
                correct=False,
                judge_reasoning=reasoning,
                tool_calls=[],
            )
            for question_id, (question, expected) in QUESTIONS.items()
        ]
        run = RunResults(
            configuration="files-plus-wiki",
            as_of=cut.as_of,
            cut=asked,
            overall=Scores.over(questions),
            categories={"single_fact_recall": Scores.over(questions)},
            questions=questions,
        )
        run_dir = tmp_path / "evals" / "runs" / name
        for number in range(1, count + 1):
            (run_dir / f"run-{number}").mkdir(parents=True)
            (run_dir / f"run-{number}" / "results.json").write_text(run.model_dump_json())
        return run_dir

    return lay


@dataclass
class Parts:
    """What a case holds beside its cut, as `pa-eval diagnose` and `pa-eval release` are both
    told it. A test adds to it, or takes from it, before it runs either."""

    eval_set: Path | None = None
    runs: dict[str, list[Path]] = field(default_factory=dict)
    """Run directories, by the label of their set."""
    wikis: dict[str, Path] = field(default_factory=dict)
    """Wiki trees, by label."""
    histories: dict[str, Path] = field(default_factory=dict)
    """Exported histories, by their wiki's label."""
    earlier: list[Path] = field(default_factory=list)
    """Earlier diagnoses' directories."""

    def arguments(self) -> list[object]:
        labelled = [
            *(("--runs", label, path) for label, paths in self.runs.items() for path in paths),
            *(("--wiki", label, path) for label, path in self.wikis.items()),
            *(("--history", label, path) for label, path in self.histories.items()),
        ]
        return [
            *(() if self.eval_set is None else ("--eval-set", self.eval_set)),
            *(part for option, label, path in labelled for part in (option, f"{label}={path}")),
            *(part for path in self.earlier for part in ("--earlier", path)),
        ]


@pytest.fixture
def parts(write_eval_set, lay_runs) -> Parts:
    """The case of a `desk`: the eval set, and the two sets of runs that `well_formed` counts
    under their labels."""
    return Parts(
        eval_set=write_eval_set(),
        runs={"own": [lay_runs("own", 3)], "dreamed": [lay_runs("dreamed", 6)]},
    )


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
    """Makes the diagnosis named `NAME`, whose output is the given directory's files."""

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
    parts: Parts,
    leavings: Callable[..., Path],
    pa_eval: Callable[..., Ran],
    monkeypatch: pytest.MonkeyPatch,
) -> Desk:
    """A release's desk: the owner is the corpus's fictional one, as the cut was taken for.
    Both commands are told the case's `parts` as they are when the command runs."""
    released = tmp_path / "released"
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    monkeypatch.setenv("PA_RELEASED_DIR", str(released))
    monkeypatch.setenv("PA_OWNER_NAME", _OWNER.name)
    monkeypatch.setenv("PA_OWNER_EMAILS", ",".join(_OWNER.email_addresses))
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", ",".join(_OWNER.other_names))
    # Before a test changes the findings it will leave.
    in_form = leavings()

    def diagnose(leaves: Path) -> Path:
        ran = pa_eval(
            *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
            *parts.arguments(),
            *("--out", data_root.diagnoses, "--agent", "scripted", "--leaves", in_form),
        )
        assert ran.code == 0
        # The output is whatever comes back from where the session ran. `pa-eval diagnose`
        # fails a diagnosis whose output is malformed, and the check never counts on that.
        output = data_root.diagnoses / NAME / "output"
        shutil.rmtree(output)
        shutil.copytree(leaves, output, symlinks=True)
        return data_root.diagnoses / NAME

    def release(*extra: object) -> Ran:
        return pa_eval("release", "--name", NAME, "--cut", cut.path, *parts.arguments(), *extra)

    return Desk(data_root.diagnoses / NAME, released / NAME, release, diagnose)


# The real launcher, shown through a stand-in `claude` on PATH. Each time it is run it records
# what the real CLI would see, and does the next of its steps: the first when the session is
# started, the next each time it is resumed, and the last again after that.

_STAND_IN_CLAUDE = """\
import json, os, shutil, sys, time
from pathlib import Path
here = Path(os.environ["PA_TEST_STAND_IN"])
steps = json.loads((here / "steps.json").read_text())
number = len(list(here.glob("seen-*.json")))
step = steps[min(number, len(steps) - 1)]
settings = json.loads(Path(".claude/settings.json").read_text())
case, output, code = map(Path, settings["permissions"]["additionalDirectories"])
everything = (case, *case.rglob("*"))
(here / f"seen-{number}.json").write_text(json.dumps({
    "argv": sys.argv[1:],
    "prompt": sys.stdin.read(),
    "cwd": os.getcwd(),
    "claude_md": Path("CLAUDE.md").read_text(),
    "settings": settings,
    "case": str(case),
    "output": str(output),
    "code": str(code),
    "case_files": sorted(str(p.relative_to(case)) for p in everything if p.is_file()),
    "earlier": {str(p.relative_to(case)): p.read_text() for p in case.glob("earlier/*/*")},
    "writable": sorted(str(p.relative_to(case)) for p in everything if os.access(p, os.W_OK)),
}))
if step.get("leaves"):
    for entry in output.iterdir():
        shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    shutil.copytree(step["leaves"], output, symlinks=True, dirs_exist_ok=True)
time.sleep(step.get("sleep", 0))
result = {"type": "result", "subtype": "success", "is_error": False,
          "result": "I left both files. Zebulon's marmalade invoice was behind it.",
          "total_cost_usd": 1.25, "num_turns": 10, "duration_ms": 60000, "session_id": "s-7c1",
          **step.get("result", {})}
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-7c1"}))
print(json.dumps(result))
sys.exit(1 if result["is_error"] else 0)
"""


@dataclass(frozen=True)
class StandIn:
    """A stand-in `claude`, and what it saw."""

    directory: Path

    def seen(self) -> list[dict[str, Any]]:
        """What it saw each time it was run, in order."""
        files = sorted(self.directory.glob("seen-*.json"), key=lambda path: int(path.stem[5:]))
        return [json.loads(path.read_text()) for path in files]


@pytest.fixture
def claude_stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., StandIn]:
    """Puts a stand-in `claude` on PATH that does the given steps, one each time it is run.

    A step is what a session does before it ends: `leaves` (a directory whose files become its
    output), `sleep` (seconds), and `result` (fields of the result it reports, over a
    success's: a session that reports `is_error` exits 1)."""

    def put(*steps: dict[str, Any]) -> StandIn:
        directory = tmp_path / "stand-in"
        (directory / "bin").mkdir(parents=True)
        (directory / "steps.json").write_text(json.dumps(steps, default=str))
        claude = directory / "bin" / "claude"
        claude.write_text(f"#!{sys.executable}\n{_STAND_IN_CLAUDE}")
        claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{directory / 'bin'}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("PA_TEST_STAND_IN", str(directory))
        return StandIn(directory)

    return put
