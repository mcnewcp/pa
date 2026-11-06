"""Eval runs over a cut: a real-data eval set asked against a frozen cut of the store, with no
ingest. Built here on a synthetic cut of the fixture corpus, with fake agents."""

from __future__ import annotations

import json
import shutil
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from pa_core.catalog import open_catalog
from pa_core.envelope import Source
from pa_core.ingest import ingest
from pa_core.status import SourceStatus, Status, write_status
from pa_evals.agent import AgentAnswer, AgentRequest, ScriptedAgent
from pa_evals.cli import main
from pa_evals.configurations import FILES_PLUS_WIKI
from pa_evals.eval_set import EvalSet, EvalSetError, load_eval_set
from pa_evals.eval_wiki import EvalWikiError, cut_wiki
from pa_evals.harness import run_eval
from pa_evals.judge import ScriptedJudge
from pa_evals.results import RunResults
from pa_evals.run_cli import DEFAULT_RUNS_DIR, WORKSPACE
from pa_home.config import DataRoot
from pa_home.cut import Cut, take_cut
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"
EVAL_SET = FIXTURES / "eval_set.json"
EVAL_WIKI = FIXTURES / "eval-wiki"
LIBRARIAN_VERSION = "fedcba987654"

DEADLINE_EMAIL = "gmail_94f6b4cbc55a3b85"


@pytest.fixture
def eval_set() -> EvalSet:
    return load_eval_set(EVAL_SET)


def make_cut(
    data_root: DataRoot, eval_set: EvalSet, *, wiki: bool = True, status: bool = True
) -> Cut:
    """A cut of a data root holding the fixture corpus, captured the day before the set's
    as-of time, its wiki (the eval wiki's tree, committed by a run), and a status file."""
    shutil.copytree(CORPUS, data_root.inbox)
    store = FilesystemL0(data_root.l0)
    captured = eval_set.as_of - timedelta(days=1)
    with open_catalog(data_root.catalog, store) as catalog:
        ingest(data_root.inbox, store, catalog, eval_set.owner.to_owner(), now=lambda: captured)
    if wiki:
        repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
        shutil.copytree(EVAL_WIKI / "wiki", data_root.wiki, dirs_exist_ok=True)
        repository.commit(f"Observe a batch\n\nLibrarian-Version: {LIBRARIAN_VERSION}\n")
    if status:
        sources = {Source.GMAIL: SourceStatus(watermark=captured, cadence=timedelta(hours=1))}
        write_status(data_root.status, Status(sources=sources), at=captured)
    return take_cut(data_root, now=eval_set.as_of)


@pytest.fixture
def cut(tmp_path: Path, eval_set: EvalSet) -> Cut:
    return make_cut(DataRoot(tmp_path / "pa-data"), eval_set)


def files_under(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_a_run_over_a_cut_answers_from_the_cuts_l0_without_ingesting(eval_set, cut):
    seen: list[tuple[dict[str, bytes], list[str]]] = []

    class LookingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            root = request.data_root.path
            seen.append((files_under(request.data_root.l0), sorted(p.name for p in root.iterdir())))
            return AgentAnswer(f"Oct 9 [ep:{DEADLINE_EMAIL}].")

    results = run_eval(
        cut, eval_set.only("hotel-block-deadline"), LookingAgent(), ScriptedJudge({})
    )

    [(l0, laid)] = seen
    assert l0 == files_under(cut.l0)
    # Nothing was ingested: no inbox, and no catalog.
    assert laid == ["l0", "status"]
    assert results.ingest is None
    assert results.cut is not None
    assert (results.cut.name, results.cut.as_of) == (cut.name, cut.as_of)
    assert results.cut.episodes == len(cut.episode_ids())
    [question] = results.questions
    assert question.unknown_citations == []
    assert question.evidence_recall == 1.0


def test_a_run_over_a_cut_lays_its_l0_and_status_with_nothing_writable(eval_set, cut):
    laid: dict[str, int] = {}
    status: list[bytes] = []

    class LookingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            root = request.data_root
            for path in [root.l0, *root.l0.rglob("*"), root.status.parent, root.status]:
                laid[path.relative_to(root.path).as_posix()] = path.stat().st_mode
            # The agent is given the cut's status as it was, not one laid at the as-of time.
            assert request.status == root.status
            status.append(root.status.read_bytes())
            return AgentAnswer("Oct 9.")

    run_eval(cut, eval_set.only("hotel-block-deadline"), LookingAgent(), ScriptedJudge({}))

    assert cut.status is not None
    assert status == [cut.status.read_bytes()]
    writable = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    assert {path for path, mode in laid.items() if mode & writable} == set()


def test_a_cut_without_a_status_file_gives_the_agent_none(tmp_path, eval_set):
    cut = make_cut(DataRoot(tmp_path / "pa-data"), eval_set, status=False)
    agent = ScriptedAgent({"hotel-block-deadline": "Oct 9."})

    run_eval(cut, eval_set.only("hotel-block-deadline"), agent, ScriptedJudge({}))

    assert [request.status for request in agent.requests] == [None]


def test_a_run_over_a_cut_leaves_the_cut_as_it_was(eval_set, cut):
    before = files_under(cut.path)

    run_eval(cut, eval_set, ScriptedAgent({}), ScriptedJudge({}))

    assert files_under(cut.path) == before


def test_files_plus_wiki_over_a_cut_gives_the_agent_the_cuts_wiki(eval_set, cut):
    wikis: list[dict[str, bytes]] = []

    class LookingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            assert request.wiki is not None
            wikis.append(files_under(request.wiki))
            return AgentAnswer("Oct 9.")

    results = run_eval(
        cut,
        eval_set.only("hotel-block-deadline"),
        LookingAgent(),
        ScriptedJudge({}),
        configuration=FILES_PLUS_WIKI,
        wiki=cut_wiki(cut),
    )

    assert cut.wiki is not None
    assert wikis == [files_under(cut.wiki)]
    assert results.wiki is not None
    assert results.wiki.librarian_version == LIBRARIAN_VERSION


def test_a_cut_without_a_wiki_has_no_wiki_to_give(tmp_path, eval_set):
    cut = make_cut(DataRoot(tmp_path / "pa-data"), eval_set, wiki=False)

    with pytest.raises(EvalWikiError, match="holds no wiki"):
        cut_wiki(cut)


# The set over a cut: every evidence id must be in it.


def test_a_set_whose_evidence_is_all_in_the_cut_loads(cut):
    assert load_eval_set(EVAL_SET, cut=cut) == load_eval_set(EVAL_SET)


def write_set(path: Path, *, evidence: list[str], adversarial: list[str] | None = None) -> Path:
    data = json.loads(EVAL_SET.read_text())
    data["questions"][0]["evidence"] = evidence
    data["adversarial"] = adversarial or []
    path.write_text(json.dumps(data))
    return path


def test_a_set_citing_an_episode_missing_from_the_cut_fails_to_load(tmp_path, cut):
    missing = "gmail_0000000000000000"
    path = write_set(tmp_path / "set.json", evidence=[DEADLINE_EMAIL, missing])

    with pytest.raises(EvalSetError, match=f"not in the cut {cut.name}: {missing}"):
        load_eval_set(path, cut=cut)


def test_a_set_naming_an_adversarial_episode_missing_from_the_cut_fails_to_load(tmp_path, cut):
    missing = "gmail_1111111111111111"
    path = write_set(tmp_path / "set.json", evidence=[DEADLINE_EMAIL], adversarial=[missing])

    with pytest.raises(EvalSetError, match=missing):
        load_eval_set(path, cut=cut)


def write_set_asked_at(path: Path, as_of: str) -> Path:
    data = json.loads(EVAL_SET.read_text())
    data["as_of"] = as_of
    path.write_text(json.dumps(data))
    return path


QUESTION_TEXT = json.loads(EVAL_SET.read_text())["questions"][0]["question"]


def test_a_set_asked_at_another_time_than_the_cut_was_taken_fails_to_load(tmp_path, cut):
    path = write_set_asked_at(tmp_path / "set.json", "2026-10-07T20:00:00-05:00")

    with pytest.raises(EvalSetError) as refused:
        load_eval_set(path, cut=cut)

    message = str(refused.value)
    assert "2026-10-07T20:00:00-05:00" in message
    assert cut.as_of.isoformat() in message
    assert QUESTION_TEXT not in message


def test_a_set_asked_at_the_cuts_time_in_another_zone_loads(tmp_path, cut):
    path = write_set_asked_at(tmp_path / "set.json", "2026-10-07T01:00:00+00:00")

    assert load_eval_set(path, cut=cut).as_of == cut.as_of


# `pa-eval run --cut DIR`

QUESTION = "hotel-block-deadline"
ANSWERS = {QUESTION: f"It closes on Oct 9 [ep:{DEADLINE_EMAIL}]."}
VERDICTS = {
    QUESTION: {"correct": True, "reasoning": "Same date.", "relevant_citations": [DEADLINE_EMAIL]}
}


@pytest.fixture
def scripts(tmp_path: Path) -> list[str]:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps(VERDICTS))
    agent = ["--agent", "scripted", "--answers", str(answers)]
    return [*agent, "--judge", "scripted", "--verdicts", str(verdicts), "--only", QUESTION]


@pytest.fixture
def private(tmp_path: Path) -> Path:
    """A private eval directory, as one under the data root is, holding a copy of the set."""
    directory = tmp_path / "private"
    directory.mkdir()
    shutil.copy(EVAL_SET, directory / "set.json")
    return directory


def pa_eval_run_over(cut: Cut, eval_set: Path, runs: Path, scripts: list[str], *extra: str) -> int:
    args = ["run", "--cut", str(cut.path), "--eval-set", str(eval_set), "--runs-dir", str(runs)]
    return main([*args, *scripts, *extra])


def test_pa_eval_run_over_a_cut_reports_the_cut_in_place_of_the_ingest(cut, scripts, private):
    runs = private / "runs"

    assert pa_eval_run_over(cut, private / "set.json", runs, scripts) == 0

    [run_dir] = runs.iterdir()
    results = RunResults.model_validate_json((run_dir / "results.json").read_text())
    assert results.ingest is None
    assert results.cut is not None
    assert results.cut.name == cut.name
    assert results.setup["cut"] == cut.name
    assert "corpus" not in results.setup
    assert results.overall.correctness == 1.0
    report = (run_dir / "report.md").read_text()
    assert f"- Cut: {cut.name}, taken {cut.as_of.isoformat()}, " in report
    assert "- Ingest:" not in report


def test_pa_eval_run_files_plus_wiki_over_a_cut_asks_with_the_cuts_wiki(cut, scripts, private):
    runs = private / "runs"
    wiki = ["--configuration", "files-plus-wiki"]

    assert pa_eval_run_over(cut, private / "set.json", runs, scripts, *wiki) == 0

    [run_dir] = runs.iterdir()
    results = RunResults.model_validate_json((run_dir / "results.json").read_text())
    assert results.wiki is not None
    assert results.wiki.librarian_version == LIBRARIAN_VERSION


def test_pa_eval_run_over_a_cut_refuses_another_wiki(cut, scripts, private, capsys):
    runs = private / "runs"
    wiki = ["--configuration", "files-plus-wiki", "--wiki", str(EVAL_WIKI)]

    assert pa_eval_run_over(cut, private / "set.json", runs, scripts, *wiki) == 1

    assert "a run over a cut asks with the cut's own wiki: drop --wiki" in capsys.readouterr().err
    assert not runs.exists()


def test_pa_eval_run_refuses_a_cut_and_a_corpus_together(cut, scripts, private, capsys):
    runs = private / "runs"

    assert pa_eval_run_over(cut, private / "set.json", runs, scripts, "--corpus", "argus") == 1

    assert "--cut and --corpus" in capsys.readouterr().err
    assert not runs.exists()


def test_pa_eval_run_over_a_cut_needs_its_eval_set(cut, scripts, private, capsys):
    args = ["run", "--cut", str(cut.path), "--runs-dir", str(private / "runs")]

    assert main([*args, *scripts]) == 1

    assert "--cut DIR needs --eval-set FILE" in capsys.readouterr().err


def test_pa_eval_run_over_a_cut_refuses_a_set_citing_an_episode_missing_from_it(
    cut, scripts, private, capsys
):
    runs = private / "runs"
    missing = "gmail_0000000000000000"
    eval_set = write_set(private / "set.json", evidence=[missing])

    assert pa_eval_run_over(cut, eval_set, runs, scripts) == 1

    assert missing in capsys.readouterr().err
    assert not runs.exists()


def test_pa_eval_run_over_a_cut_refuses_a_set_asked_at_another_time(cut, scripts, private, capsys):
    runs = private / "runs"
    eval_set = write_set_asked_at(private / "set.json", "2026-10-05T20:00:00-05:00")

    assert pa_eval_run_over(cut, eval_set, runs, scripts) == 1

    error = capsys.readouterr().err
    assert "2026-10-05T20:00:00-05:00" in error and cut.as_of.isoformat() in error
    assert QUESTION_TEXT not in error
    assert not runs.exists()


# The repository guard: nothing of a real-data run may be inside the repository.

INSIDE = WORKSPACE / "evals" / "runs" / "private-test-never-written"


@pytest.mark.parametrize("option", ["--cut", "--eval-set", "--runs-dir"])
def test_pa_eval_run_over_a_cut_refuses_paths_inside_the_repository(
    cut, scripts, private, capsys, option
):
    runs = private / "runs"
    paths = {"--cut": cut.path, "--eval-set": private / "set.json", "--runs-dir": runs}
    paths[option] = INSIDE
    args = ["run", *(part for name, path in paths.items() for part in (name, str(path)))]

    assert main([*args, *scripts]) == 1

    assert f"{option} ({INSIDE}) is inside the repository" in capsys.readouterr().err
    assert not runs.exists()
    assert not INSIDE.exists()


def test_pa_eval_run_over_a_cut_refuses_the_default_runs_directory(cut, scripts, private, capsys):
    args = ["run", "--cut", str(cut.path), "--eval-set", str(private / "set.json")]

    assert main([*args, *scripts]) == 1

    assert f"--runs-dir ({DEFAULT_RUNS_DIR}) is inside the repository" in capsys.readouterr().err


def test_the_guard_sees_through_a_link_into_the_repository(cut, scripts, private, capsys):
    link = private / "runs-link"
    link.symlink_to(WORKSPACE / "evals", target_is_directory=True)

    assert pa_eval_run_over(cut, private / "set.json", link / "runs", scripts) == 1

    assert "is inside the repository" in capsys.readouterr().err
