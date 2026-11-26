"""The whole case of a diagnosis (`pa-eval diagnose`): beside the cut, an eval set, sets of eval
runs and wikis under the referral's labels, each wiki's history, and earlier diagnoses' private
analyses. Each is laid read-only where the diagnostician's instructions say, and refused inside
the repository. The case here is synthetic, as `conftest` builds it."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from pa_evals.case import Case, CaseWiki
from pa_evals.diagnosis import DiagnosisRequest
from pa_evals.diagnosis import diagnose as diagnose_over
from pa_home.config import DataRoot
from pa_home.cut import Cut
from pa_home.project import REPOSITORY
from pa_home.project_session import SessionReply

NAME = "dreamed-drop"


class Looking:
    """A diagnostician that looks at what it was given, and leaves the files of `leaves`."""

    def __init__(self, leaves: Path) -> None:
        self.leaves = leaves

    def diagnose(self, request: DiagnosisRequest) -> SessionReply:
        self.request = request
        shutil.copytree(self.leaves, request.output, dirs_exist_ok=True)
        case = request.case
        self.files = sorted(str(p.relative_to(case)) for p in case.rglob("*") if p.is_file())
        self.writable = [str(p) for p in (case, *case.rglob("*")) if os.access(p, os.W_OK)]
        self.read = {
            name: (case / name).read_text()
            for name in self.files
            if not name.startswith(("cut/", "wikis/own/wiki/"))
        }
        return SessionReply(
            text="Done.", cost_usd=None, turns=None, duration_ms=None, tool_calls=()
        )


@pytest.fixture
def dreamed_wiki(tmp_path: Path, cut: Cut) -> Path:
    """A copy of the cut's wiki that dreaming reworked: a page gone, and a page of its own."""
    assert cut.wiki is not None
    tree = tmp_path / "copies" / "dreamed" / "wiki"
    tree.parent.mkdir(parents=True)
    shutil.copytree(cut.wiki, tree)
    (tree / "topics" / "daves-wedding.md").unlink()
    (tree / "topics" / "the-wedding.md").write_text("# The wedding\n")
    return tree


@pytest.fixture
def history(tmp_path: Path) -> Callable[[str], Path]:
    """A wiki's history, as `pa-eval wiki-history` leaves one: a file per run."""

    def export(label: str) -> Path:
        directory = tmp_path / "histories" / label
        directory.mkdir(parents=True)
        (directory / "0001-0123456789ab.patch").write_text("commit 0123456789ab\n\nStart\n")
        (directory / "0002-ba9876543210.patch").write_text("commit ba9876543210\n\nA run\n")
        return directory

    return export


@pytest.fixture
def earlier(data_root: DataRoot) -> Path:
    """An earlier diagnosis, as it was left under the data root."""
    directory = data_root.diagnoses / "first-look"
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "analysis.md").write_text("# What the first look found\n")
    (directory / "output" / "findings.json").write_text("{}")
    (directory / "check-notes.md").write_text("What the check found.\n")
    return directory


def test_each_part_of_the_case_is_laid_read_only_under_its_label(
    tmp_path: Path,
    cut: Cut,
    referral: Path,
    parts,
    lay_runs,
    dreamed_wiki,
    history,
    earlier,
    leavings,
):
    assert cut.wiki is not None and parts.eval_set is not None
    case = Case(
        cut=cut,
        eval_set=parts.eval_set,
        # Two run directories under one label are one set of runs.
        runs={"own": parts.runs["own"], "dreamed": [*parts.runs["dreamed"], lay_runs("more", 2)]},
        wikis={
            "own": CaseWiki(cut.wiki, history("own")),
            "dreamed": CaseWiki(dreamed_wiki),
        },
        earlier={"first-look": earlier / "output" / "analysis.md"},
    )
    looking = Looking(leavings())

    diagnose_over(
        referral.read_text(),
        case,
        looking,
        name=NAME,
        directory=tmp_path / "diagnoses",
        commit="0" * 40,
    )

    beside_the_cut = [name for name in looking.files if not name.startswith("cut/")]
    wiki = sorted(str(p.relative_to(cut.wiki)) for p in cut.wiki.rglob("*") if p.is_file())
    dreamed = sorted(str(p.relative_to(dreamed_wiki)) for p in dreamed_wiki.rglob("*"))
    assert beside_the_cut == sorted(
        [
            "earlier/first-look/analysis.md",
            "eval-set.json",
            *(f"runs/dreamed/run-{n}/results.json" for n in range(1, 9)),
            *(f"runs/own/run-{n}/results.json" for n in range(1, 4)),
            *(f"wikis/dreamed/wiki/{path}" for path in dreamed if (dreamed_wiki / path).is_file()),
            "wikis/own/history/0001-0123456789ab.patch",
            "wikis/own/history/0002-ba9876543210.patch",
            *(f"wikis/own/wiki/{path}" for path in wiki),
        ]
    )
    # Each part as it is: the eval set with its expected answers, a run's answers, a wiki's
    # own pages, a run of history, and the earlier diagnosis's private analysis alone.
    assert json.loads(looking.read["eval-set.json"]) == json.loads(parts.eval_set.read_text())
    answered = json.loads(looking.read["runs/dreamed/run-8/results.json"])["questions"][0]
    assert (answered["id"], answered["answer"]) == ("r07", "The one for the jam.")
    assert looking.read["wikis/dreamed/wiki/topics/the-wedding.md"] == "# The wedding\n"
    assert "wikis/dreamed/wiki/topics/daves-wedding.md" not in looking.files
    assert looking.read["wikis/own/history/0002-ba9876543210.patch"].endswith("A run\n")
    assert looking.read["earlier/first-look/analysis.md"] == "# What the first look found\n"
    # The cut is where it was, and nothing in the case is writable.
    assert "cut/cut.json" in looking.files
    assert looking.writable == []
    assert not looking.request.case.exists()


def diagnose(pa_eval, referral: Path, cut: Cut, out: Path, *extra: object):
    return pa_eval(
        *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME, "--out", out),
        *extra,
    )


def test_the_command_lays_each_part_it_is_given_where_the_instructions_say(
    data_root: DataRoot,
    cut: Cut,
    referral: Path,
    parts,
    dreamed_wiki,
    history,
    earlier,
    leavings,
    claude_stand_in,
    pa_eval,
):
    parts.wikis.update(own=cut.wiki, dreamed=dreamed_wiki)
    parts.histories["dreamed"] = history("dreamed")
    parts.earlier.append(earlier)
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = diagnose(
        pa_eval, referral, cut, data_root.diagnoses, *parts.arguments(), "--agent", "claude"
    )

    assert ran.code == 0
    (seen,) = stand_in.seen()
    laid = set(seen["case_files"])
    assert {
        "cut/cut.json",
        "eval-set.json",
        "runs/own/run-3/results.json",
        "runs/dreamed/run-6/results.json",
        "wikis/own/wiki/topics/daves-wedding.md",
        "wikis/dreamed/wiki/topics/the-wedding.md",
        "wikis/dreamed/history/0002-ba9876543210.patch",
        "earlier/first-look/analysis.md",
    } <= laid
    assert seen["writable"] == []
    # Of an earlier diagnosis, only its private analysis.
    assert [name for name in laid if name.startswith("earlier/")] == [
        "earlier/first-look/analysis.md"
    ]
    # The instructions name the layout, so a referral can say "the runs under `dreamed`".
    instructions = seen["claude_md"]
    assert f"The case is at `{seen['case']}`" in instructions
    for where in (
        "`eval-set.json`",
        "`runs/<label>/run-<n>/results.json`",
        "`wikis/<label>/wiki/`",
        "`wikis/<label>/history/`",
        "`earlier/<name>/analysis.md`",
    ):
        assert where in instructions
    # And they point at the code as deployed, which the session may read.
    assert seen["code"] == str(REPOSITORY)
    assert f"`{REPOSITORY}`" in instructions
    assert "`librarian/`" in instructions and "`agent/`" in instructions
    assert "$" not in instructions
    # What the command prints says what the case held, by label and count.
    assert (
        "case: an eval set; runs own (3), dreamed (6); wikis own, dreamed (2 runs of history); "
        "earlier first-look"
    ) in ran.out


def test_a_follow_up_diagnosis_is_given_the_earlier_ones_private_analysis(
    data_root: DataRoot, cut: Cut, referral: Path, leavings, claude_stand_in, pa_eval
):
    first = leavings()
    made = pa_eval(
        *("diagnose", "--referral", referral, "--cut", cut.path, "--name", "first-look"),
        *("--out", data_root.diagnoses, "--agent", "scripted", "--leaves", first),
    )
    assert made.code == 0
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = diagnose(
        pa_eval,
        referral,
        cut,
        data_root.diagnoses,
        *("--earlier", data_root.diagnoses / "first-look", "--agent", "claude"),
    )

    assert ran.code == 0
    (seen,) = stand_in.seen()
    # The earlier diagnosis's private analysis, under its name, and nothing else of it.
    assert seen["earlier"] == {
        "earlier/first-look/analysis.md": (first / "analysis.md").read_text()
    }
    assert "case: earlier first-look" in ran.out
    session = json.loads((data_root.diagnoses / NAME / "session.json").read_text())
    assert session["case"]["earlier"] == ["first-look"]


INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-case-test-never-written"

PARTS = {
    "--eval-set": lambda inside: ["--eval-set", inside],
    "--runs": lambda inside: ["--runs", f"dreamed={inside}"],
    "--wiki": lambda inside: ["--wiki", f"dreamed={inside}"],
    "--history": lambda inside: ["--wiki", "dreamed=/nowhere", "--history", f"dreamed={inside}"],
    "--earlier": lambda inside: ["--earlier", inside],
}


@pytest.mark.parametrize("option", PARTS)
def test_each_part_of_the_case_is_refused_inside_the_repository(
    tmp_path: Path, data_root: DataRoot, cut: Cut, referral: Path, leavings, pa_eval, option: str
):
    # Through a link, too: the guard follows it.
    link = tmp_path / "linked"
    link.symlink_to(INSIDE.parent, target_is_directory=True)

    for inside in (INSIDE, link / INSIDE.name):
        ran = diagnose(
            pa_eval,
            referral,
            cut,
            data_root.diagnoses,
            *PARTS[option](inside),
            *("--agent", "scripted", "--leaves", leavings()),
        )

        assert ran.code == 1
        named = option if option in ("--eval-set", "--earlier") else f"{option} dreamed"
        assert f"{named} ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()
    assert not data_root.diagnoses.exists()


def not_a_part(tmp_path: Path, cut: Cut, lay_runs, write_eval_set) -> dict[str, tuple[list, str]]:
    """Parts that aren't what they are named as, each with what the command says of it."""
    empty = tmp_path / "empty"
    empty.mkdir()
    other_time = write_eval_set()
    other_time.write_text(other_time.read_text().replace(cut.as_of.isoformat()[:4], "2031"))
    not_results = lay_runs("not-results", 1)
    (not_results / "run-1" / "results.json").write_text('{"questions": "gooseberry"}')
    return {
        "an eval set asked at another time": (["--eval-set", other_time], "but the cut"),
        "a run directory with no runs": (["--runs", f"own={empty}"], "doesn't hold eval runs"),
        "results that aren't results": (
            ["--runs", f"own={not_results}"],
            "doesn't hold eval runs",
        ),
        "runs over another cut": (
            ["--runs", f"own={lay_runs('elsewhere', 2, over='20250101T000000Z')}"],
            f"wasn't asked over the cut {cut.name}",
        ),
        "a label that isn't one": (["--runs", f"The Dreamed={empty}"], "a label is lowercase"),
        "a part with no label": (["--runs", empty], "--runs takes LABEL=DIR"),
        "a wiki that isn't there": (
            ["--wiki", f"dreamed={tmp_path / 'nowhere'}"],
            "the wiki under dreamed",
        ),
        "one label for two wikis": (
            ["--wiki", f"dreamed={empty}", "--wiki", f"dreamed={empty}"],
            "--wiki names one label twice",
        ),
        "a history with no wiki": (["--history", f"dreamed={empty}"], "and no wiki"),
        "an earlier diagnosis with no analysis": (
            ["--earlier", empty],
            "the earlier diagnosis empty has no private analysis",
        ),
    }


@pytest.mark.parametrize("which", range(10))
def test_a_part_that_isnt_what_it_is_named_as_is_refused_before_a_session_starts(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    referral: Path,
    lay_runs,
    write_eval_set,
    leavings,
    pa_eval,
    which: int,
):
    cases = not_a_part(tmp_path, cut, lay_runs, write_eval_set)
    assert len(cases) == 10
    arguments, said = list(cases.values())[which]

    ran = diagnose(
        pa_eval,
        referral,
        cut,
        data_root.diagnoses,
        *arguments,
        *("--agent", "scripted", "--leaves", leavings()),
    )

    assert ran.code == 1
    assert said in ran.err
    # Nothing a part holds is quoted, and no diagnosis takes the name.
    assert "gooseberry" not in ran.err
    assert not data_root.diagnoses.exists()


# The live store is never a part of a case: only a cut of it, and copies of the cut's wiki.


def as_a_cut(tmp_path: Path, data_root: DataRoot, cut: Cut) -> dict[str, Path]:
    """Directories that hold a cut's manifest and aren't cuts."""
    # The store itself, with a manifest in it.
    shutil.copyfile(cut.path / "cut.json", data_root.path / "cut.json")
    # A store laid out elsewhere (a restored backup, say), named as the cut is.
    restored = tmp_path / "restored" / cut.name
    shutil.copytree(cut.path, restored)
    (restored / "inbox").mkdir()
    # A cut under another name than the time it was taken.
    renamed = tmp_path / "renamed" / "latest"
    shutil.copytree(cut.path, renamed)
    return {"the store": data_root.path, "a store elsewhere": restored, "another name": renamed}


@pytest.mark.parametrize("which", ["the store", "a store elsewhere", "another name"])
def test_a_directory_that_isnt_a_cut_is_refused_as_the_cut_before_a_session_starts(
    tmp_path: Path, data_root: DataRoot, cut: Cut, referral: Path, leavings, pa_eval, which: str
):
    not_a_cut = as_a_cut(tmp_path, data_root, cut)[which]

    ran = pa_eval(
        *("diagnose", "--referral", referral, "--cut", not_a_cut, "--name", "dreamed-drop"),
        *("--out", tmp_path / "diagnoses", "--agent", "scripted", "--leaves", leavings()),
    )

    assert ran.code == 1
    assert f"{not_a_cut} isn't a cut" in ran.err
    assert not (tmp_path / "diagnoses").exists()


def test_the_live_wiki_is_refused_as_a_wiki_of_the_case_before_a_session_starts(
    data_root: DataRoot, cut: Cut, referral: Path, leavings, pa_eval
):
    ran = diagnose(
        pa_eval,
        referral,
        cut,
        data_root.diagnoses,
        *("--wiki", f"own={data_root.wiki}"),
        *("--agent", "scripted", "--leaves", leavings()),
    )

    assert ran.code == 1
    assert f"the wiki under own ({data_root.wiki}) is a live store's" in ran.err
    assert not data_root.diagnoses.exists()
