import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pa_evals import frozen
from pa_evals.cli import main
from pa_evals.eval_set import load_eval_set
from pa_evals.run_cli import DEFAULT_RUNS_DIR

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"
EVAL_SET = FIXTURES / "eval_set.json"

DEADLINE_EMAIL = "gmail_94f6b4cbc55a3b85"
FIRST_EMAIL = "gmail_424bdea9b0d82720"
SWIM_EVENT = "icloud_calendar_9d69b653f9a42b18"

ANSWERS = {
    "hotel-block-deadline": f"It closes on Oct 9 [ep:{DEADLINE_EMAIL}].",
    "swim-lessons-plan": f"Saturday Oct 17 at 9 [ep:{SWIM_EVENT}] [ep:gmail_0000000000000000].",
    "who-asked-about-hotel": {"error": "claude -p timed out after 300 s"},
    "swim-instructor": "I don't know; nothing I have names the instructor.",
    "day-of-week": "It's Saturday.",
}
VERDICTS = {
    "hotel-block-deadline": {
        "correct": True,
        "reasoning": "Same date.",
        "relevant_citations": [DEADLINE_EMAIL],
    },
    "swim-lessons-plan": {
        "correct": False,
        "reasoning": "Misses the registration commitment.",
        "relevant_citations": [SWIM_EVENT],
    },
    "swim-instructor": {"correct": True, "reasoning": "Abstains.", "relevant_citations": []},
    "day-of-week": {"correct": True, "reasoning": "Saturday.", "relevant_citations": []},
}


@pytest.fixture
def scripts(tmp_path: Path) -> list[str]:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS))
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps(VERDICTS))
    agent = ["--agent", "scripted", "--answers", str(answers)]
    return [*agent, "--judge", "scripted", "--verdicts", str(verdicts)]


def pa_eval_run(runs: Path, scripts: list[str], *extra: str) -> int:
    args = ["run", "--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)]
    return main([*args, *scripts, *extra])


def test_run_writes_a_report_and_results_into_a_timestamped_run_directory(
    tmp_path, scripts, capsys
):
    runs = tmp_path / "runs"

    assert pa_eval_run(runs, scripts) == 0

    [run_dir] = runs.iterdir()
    assert run_dir.name.endswith("Z")
    assert sorted(p.name for p in run_dir.iterdir()) == ["report.md", "results.json"]
    results = json.loads((run_dir / "results.json").read_text())
    assert [q["id"] for q in results["questions"]] == list(ANSWERS)
    assert results["overall"]["questions"] == 5
    assert results["overall"]["failed"] == 1
    assert results["overall"]["correctness"] == 0.6
    assert results["categories"]["cross_channel_synthesis"]["evidence_recall"] == 0.5
    assert results["categories"]["cross_channel_synthesis"]["citation_validity"] == 0.5
    assert {k: v for k, v in results["setup"].items() if k != "commit"} == {
        "agent": "scripted",
        "judge": "scripted",
    }
    assert str(run_dir) in capsys.readouterr().out


def test_the_report_shows_scores_answers_and_the_judges_reasoning(tmp_path, scripts):
    runs = tmp_path / "runs"
    pa_eval_run(runs, scripts)

    [run_dir] = runs.iterdir()
    report = (run_dir / "report.md").read_text()

    assert "| cross_channel_synthesis | 1 | 0 | 0.00 | 0.50 | 0.50 |" in report
    assert "| attribution | 1 | 1 | 0.00 | 0.00 | n/a |" in report
    assert "| who-asked-about-hotel | attribution | failed |" in report
    assert "claude -p timed out after 300 s" in report
    assert "Misses the registration commitment." in report
    assert "> It closes on Oct 9 [ep:gmail_94f6b4cbc55a3b85]." in report
    assert "gmail_0000000000000000" in report


def test_the_report_shows_paraphrase_questions_as_their_own_category(tmp_path, scripts):
    data = json.loads(EVAL_SET.read_text())
    [question] = [q for q in data["questions"] if q["id"] == "hotel-block-deadline"]
    question["category"] = "paraphrase"
    eval_set = tmp_path / "eval_set.json"
    eval_set.write_text(json.dumps(data))
    runs = tmp_path / "runs"

    args = ["run", "--corpus", str(CORPUS), "--eval-set", str(eval_set), "--runs-dir", str(runs)]
    assert main([*args, *scripts]) == 0

    [run_dir] = runs.iterdir()
    report = (run_dir / "report.md").read_text()
    assert "| paraphrase | 1 | 0 | 1.00 | 1.00 | 1.00 |" in report
    assert "| hotel-block-deadline | paraphrase | scored |" in report


def test_two_runs_over_the_same_inputs_write_identical_outputs(tmp_path, scripts):
    runs = tmp_path / "runs"

    pa_eval_run(runs, scripts)
    pa_eval_run(runs, scripts)

    first, second = sorted(runs.iterdir())
    assert first != second
    for name in ("report.md", "results.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_run_can_be_narrowed_to_some_questions_and_concurrency_is_configurable(tmp_path, scripts):
    runs = tmp_path / "runs"

    pa_eval_run(runs, scripts, "--only", "day-of-week", "--only", "swim-instructor")
    pa_eval_run(runs, scripts, "--concurrency", "1")

    narrowed, serial = sorted(runs.iterdir())
    results = json.loads((narrowed / "results.json").read_text())
    assert [q["id"] for q in results["questions"]] == ["swim-instructor", "day-of-week"]
    assert json.loads((serial / "results.json").read_text())["overall"]["questions"] == 5


def test_run_never_touches_the_configured_data_root(tmp_path, scripts, monkeypatch):
    configured = tmp_path / "pa-data"
    configured.mkdir()
    monkeypatch.setenv("PA_DATA_DIR", str(configured))

    assert pa_eval_run(tmp_path / "runs", scripts) == 0

    assert list(configured.iterdir()) == []


def test_a_missing_eval_set_is_reported_without_a_traceback(tmp_path, scripts, capsys):
    args = ["run", "--corpus", str(CORPUS), "--eval-set", str(tmp_path / "nope.json")]

    assert main([*args, "--runs-dir", str(tmp_path / "runs"), *scripts]) == 1

    assert "eval set not found" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_run_outputs_in_the_default_runs_directory_are_ignored_by_git():
    repository = DEFAULT_RUNS_DIR.parent.parent
    if not (repository / ".git").exists():
        pytest.skip("not running from a git checkout")
    run_output = DEFAULT_RUNS_DIR / "2026-10-06T200000Z" / "report.md"

    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", str(run_output)],
        cwd=repository,
        check=False,
    )

    assert ignored.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_report_says_which_commit_the_run_ran_from(tmp_path, scripts):
    repository = DEFAULT_RUNS_DIR.parent.parent
    if not (repository / ".git").exists():
        pytest.skip("not running from a git checkout")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.strip()
    runs = tmp_path / "runs"

    pa_eval_run(runs, scripts)

    [run_dir] = runs.iterdir()
    commit = json.loads((run_dir / "results.json").read_text())["setup"]["commit"]
    assert re.fullmatch(rf"{head}( \(with uncommitted changes\))?", commit)
    assert f"- Commit: {commit}\n" in (run_dir / "report.md").read_text()


def test_run_defaults_to_the_frozen_corpus_and_eval_set(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"day-of-week": "It's Wednesday."}))
    verdicts = tmp_path / "verdicts.json"
    verdict = {"correct": True, "reasoning": "Wednesday.", "relevant_citations": []}
    verdicts.write_text(json.dumps({"day-of-week": verdict}))
    runs = tmp_path / "runs"

    status = main(
        [
            "run",
            *["--runs-dir", str(runs), "--only", "day-of-week"],
            *["--agent", "scripted", "--answers", str(answers)],
            *["--judge", "scripted", "--verdicts", str(verdicts)],
        ]
    )

    assert status == 0
    [run_dir] = runs.iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    assert results["ingest"] == {
        "ingested": len([path for path in frozen.PAYLOADS.rglob("*") if path.is_file()]),
        "unchanged": 0,
        "quarantined": 0,
    }
    assert results["as_of"] == load_eval_set(frozen.EVAL_SET).as_of.isoformat()
    assert [question["id"] for question in results["questions"]] == ["day-of-week"]
