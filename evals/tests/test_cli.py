import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pa_evals import frozen
from pa_evals.cli import main
from pa_evals.eval_set import load_eval_set
from pa_evals.report import render_report
from pa_evals.results import RunResults
from pa_evals.run_cli import DEFAULT_RUNS_DIR

FIXTURES = Path(__file__).parent / "fixtures"
BASELINES = Path(__file__).parents[1] / "baselines"
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
        "corpus": str(CORPUS),
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


@pytest.mark.parametrize("corpus", [[], ["--corpus", "argus"]], ids=["default", "by name"])
def test_run_chooses_the_argus_corpus_and_its_eval_set_by_default_or_by_name(tmp_path, corpus):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"day-of-week": "It's Wednesday."}))
    verdicts = tmp_path / "verdicts.json"
    verdict = {"correct": True, "reasoning": "Wednesday.", "relevant_citations": []}
    verdicts.write_text(json.dumps({"day-of-week": verdict}))
    runs = tmp_path / "runs"

    status = main(
        [
            "run",
            *corpus,
            *["--runs-dir", str(runs), "--only", "day-of-week"],
            *["--agent", "scripted", "--answers", str(answers)],
            *["--judge", "scripted", "--verdicts", str(verdicts)],
        ]
    )

    assert status == 0
    [run_dir] = runs.iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    assert results["setup"]["corpus"] == "argus"
    assert "- Corpus: argus\n" in (run_dir / "report.md").read_text()
    assert results["ingest"] == {
        "ingested": len([path for path in frozen.PAYLOADS.rglob("*") if path.is_file()]),
        "unchanged": 0,
        "quarantined": 0,
    }
    assert results["as_of"] == load_eval_set(frozen.EVAL_SET).as_of.isoformat()
    assert [question["id"] for question in results["questions"]] == ["day-of-week"]


def test_a_corpus_directory_needs_its_eval_set(tmp_path, scripts, capsys):
    args = ["run", "--corpus", str(CORPUS), "--runs-dir", str(tmp_path / "runs"), *scripts]

    assert main(args) == 1

    assert "--corpus DIR needs --eval-set FILE" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_an_unknown_corpus_name_is_refused(tmp_path, scripts, capsys):
    args = ["run", "--corpus", "atlantis", "--runs-dir", str(tmp_path / "runs"), *scripts]

    assert main(args) == 1

    assert "atlantis is neither a corpus (argus) nor a directory" in capsys.readouterr().err


def test_the_report_shows_each_answers_cost_turns_and_duration_and_sums_them_up_by_category(
    tmp_path,
):
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "hotel-block-deadline": {
                    "answer": f"It closes on Oct 9 [ep:{DEADLINE_EMAIL}].",
                    "cost_usd": 0.0421,
                    "turns": 6,
                    "duration_ms": 18250,
                },
                "day-of-week": {
                    "answer": "It's Saturday.",
                    "cost_usd": 0.0105,
                    "turns": 1,
                    "duration_ms": 4000,
                },
                "swim-instructor": "I don't know.",
            }
        )
    )
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps(VERDICTS))
    runs = tmp_path / "runs"

    pa_eval_run(
        runs,
        ["--agent", "scripted", "--answers", str(answers)],
        *("--judge", "scripted", "--verdicts", str(verdicts)),
        *("--only", "hotel-block-deadline", "--only", "swim-instructor", "--only", "day-of-week"),
    )

    [run_dir] = runs.iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    [deadline, *_] = results["questions"]
    assert (deadline["cost_usd"], deadline["turns"], deadline["duration_ms"]) == (0.0421, 6, 18250)
    report = (run_dir / "report.md").read_text()
    assert "| hotel-block-deadline | single_fact_recall | scored | yes | 1.00 | 1.00 | " in report
    assert "| $0.042 | 6 | 18.2 s |" in report
    assert "| swim-instructor | abstention | scored | yes | n/a | n/a | n/a | n/a | n/a |" in report
    assert "## Efficiency by category" in report
    assert "| single_fact_recall | $0.042 | 6 | 18.2 s | $0.042 |" in report
    assert "| abstention | n/a | n/a | n/a | n/a |" in report
    # Overall: the medians of the two measured answers, and their sum.
    assert "| Overall | $0.026 | 3.5 | 11.1 s | $0.053 |" in report
    assert "- Cost: $0.042, 6 turns, 18.2 s" in report


def repeated_scripts(tmp_path: Path, name: str, deadline: list[bool], canary: list[bool]):
    """Scripts for `--runs len(deadline)`: per run, whether each of two questions is right."""

    def verdict(right: bool) -> dict:
        return {"correct": right, "reasoning": "ok" if right else "no", "relevant_citations": []}

    answers = tmp_path / f"{name}-answers.json"
    answers.write_text(
        json.dumps(
            {
                "hotel-block-deadline": [
                    {"answer": "Oct 9.", "cost_usd": 0.1, "turns": 4, "duration_ms": 10000},
                    {"answer": "Oct 8.", "cost_usd": 0.2, "turns": 8, "duration_ms": 30000},
                    {"answer": "Oct 9.", "cost_usd": 0.3, "turns": 12, "duration_ms": 50000},
                ][: len(deadline)],
                "day-of-week": "It's Saturday.",
            }
        )
    )
    verdicts = tmp_path / f"{name}-verdicts.json"
    verdicts.write_text(
        json.dumps(
            {
                "hotel-block-deadline": [verdict(right) for right in deadline],
                "day-of-week": [verdict(right) for right in canary],
            }
        )
    )
    only = ["--only", "hotel-block-deadline", "--only", "day-of-week"]
    return [
        *("--agent", "scripted", "--answers", str(answers)),
        *("--judge", "scripted", "--verdicts", str(verdicts)),
        *only,
    ]


def test_a_run_of_n_repeats_writes_each_run_and_a_comparison_of_them(tmp_path, capsys):
    runs = tmp_path / "runs"
    scripts = repeated_scripts(tmp_path, "a", [True, False, True], [True, True, True])

    assert pa_eval_run(runs, scripts, "--runs", "3") == 0

    [run_dir] = runs.iterdir()
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "comparison.md",
        "run-1",
        "run-2",
        "run-3",
    ]
    second = json.loads((run_dir / "run-2" / "results.json").read_text())
    assert second["configuration"] == "files-alone"
    assert second["questions"][0]["answer"] == "Oct 8."
    assert second["questions"][0]["correct"] is False
    assert (run_dir / "run-2" / "report.md").exists()
    comparison = (run_dir / "comparison.md").read_text()
    assert "| Category | files-alone |" in comparison
    assert "| single_fact_recall | 0.67 (0.00 to 1.00) |" in comparison
    assert "| canary | 1.00 (1.00 to 1.00) |" in comparison
    assert "| Overall | 0.83 (0.50 to 1.00) |" in comparison
    # Medians over every answer in every run that reports them.
    assert "| single_fact_recall | $0.200, 8 turns, 30.0 s |" in comparison
    assert "| canary | n/a |" in comparison
    assert f"comparison: {run_dir / 'comparison.md'}" in capsys.readouterr().out


def test_compare_marks_only_the_differences_larger_than_the_spread(tmp_path, capsys):
    pa_eval_run(
        tmp_path / "a",
        repeated_scripts(tmp_path, "a", [True, False, True], [True, True, True]),
        *("--runs", "3"),
    )
    pa_eval_run(
        tmp_path / "b",
        repeated_scripts(tmp_path, "b", [True, True, True], [False, False, False]),
        *("--runs", "3"),
    )
    [first] = (tmp_path / "a").iterdir()
    [second] = (tmp_path / "b").iterdir()
    capsys.readouterr()

    assert main(["compare", str(first), str(second)]) == 0

    comparison = capsys.readouterr().out
    assert f"| Category | {first} | {second} | {second} vs {first} |" in comparison
    # Better by 0.33, but the first's runs spread from 0 to 1: it doesn't count.
    assert "| single_fact_recall | 0.67 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | +0.33 |" in (
        comparison
    )
    # Worse by 1.00 with no spread on either side: it counts.
    assert "| canary | 1.00 (1.00 to 1.00) | 0.00 (0.00 to 0.00) | -1.00 (counts) |" in (comparison)
    assert "| Overall | 0.83 (0.50 to 1.00) | 0.50 (0.50 to 0.50) | -0.33 |" in comparison
    assert f"- {first}: 3 runs (configuration files-alone)" in comparison


def test_the_committed_baselines_load_in_a_comparison_without_efficiency_figures(capsys):
    v010 = BASELINES / "v0.10"
    v011 = BASELINES / "v0.11"

    assert main(["compare", str(v010), str(v011)]) == 0

    comparison = capsys.readouterr().out
    assert f"- {v010}: 1 run\n" in comparison
    # A single run has no spread, so no difference counts, however large.
    assert "| Overall | 0.90 (0.90 to 0.90) | 0.96 (0.96 to 0.96) | +0.06 |" in comparison
    assert "| entity_resolution | 0.50 (0.50 to 0.50) | 0.50 (0.50 to 0.50) | +0.00 |" in (
        comparison
    )
    # v0.10 asked no paraphrase questions.
    assert "| paraphrase | n/a | 1.00 (1.00 to 1.00) | n/a |" in comparison
    assert "| Overall | n/a | n/a |" in comparison


@pytest.mark.parametrize("baseline", ["v0.10", "v0.10/extended", "v0.11"])
def test_each_committed_baseline_still_renders_as_a_report(baseline):
    results = RunResults.model_validate_json((BASELINES / baseline / "results.json").read_text())

    report = render_report(results)

    assert results.configuration is None
    assert results.questions[0].cost_usd is None
    assert "| Overall | n/a | n/a | n/a | n/a |" in report


@pytest.fixture
def librarian_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `claude` stand-in on PATH for the librarian's calls: it finds nothing worth recording
    in any episode, and logs each call to the file returned."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "claude-calls.log"
    script = bin_dir / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, sys
            schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1])
            sys.stdin.read()
            properties = schema["properties"]
            if "candidate" in properties:
                output = {{"candidate": None}}
            elif "relevant" in properties:
                output = {{"relevant": False, "reason": "stand-in"}}
            elif "placements" in properties:
                output = {{"placements": []}}
            else:
                output = {{"statements": []}}
            with open({str(log)!r}, "a") as calls:
                calls.write(json.dumps(sorted(properties)) + "\\n")
            print(json.dumps({{"type": "result", "is_error": False, "result": "",
                              "structured_output": output}}))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_a_graph_configuration_builds_the_graph_and_a_second_run_hits_the_librarians_cache(
    tmp_path, scripts, librarian_claude
):
    runs = tmp_path / "runs"
    cache = tmp_path / "librarian-cache"
    graph = ["--configuration", "graph-plus-files", "--librarian-cache", str(cache)]

    assert pa_eval_run(runs / "first", scripts, "--only", "hotel-block-deadline", *graph) == 0
    calls = librarian_claude.read_text().count("\n")
    assert pa_eval_run(runs / "second", scripts, "--only", "hotel-block-deadline", *graph) == 0

    # The fixture corpus's two emails, daily note, and calendar event, extracted once.
    assert calls == librarian_claude.read_text().count("\n") == 4
    [first] = (runs / "first").iterdir()
    [second] = (runs / "second").iterdir()
    assert "| extraction | 0 | 4 | claude-sonnet-5-5: 4 |" in (first / "report.md").read_text()
    report = (second / "report.md").read_text()
    assert "- Configuration: graph-plus-files" in report
    assert f"- Librarian cache: {cache}" in report
    assert "| extraction | 4 | 0 | none |" in report
    results = json.loads((second / "results.json").read_text())
    assert results["librarian"]["model_calls"] == {}


def test_compare_says_when_a_directory_holds_no_runs(tmp_path, capsys):
    assert main(["compare", str(tmp_path)]) == 1

    assert f"no eval results in {tmp_path}" in capsys.readouterr().err
