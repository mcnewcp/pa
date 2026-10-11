"""`pa-eval run --agent claude`: the real agent runner, over a stand-in `claude` on PATH.

The stand-in answers with what it was told about "now", so the results show which time each
question was asked at. The real CLI is exercised only by the opt-in smoke test at the end.
"""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from pa_evals.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"
EVAL_SET = FIXTURES / "eval_set.json"

# Why the default won, as docs/adr/0003 records it.
WHY_SYSTEM_PROMPT = (
    "the default: it and a preamble both passed every as-of canary, and it leaves the question "
    "exactly as asked and works in an interactive session too"
)


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `claude` on PATH that answers with the system prompt it was given, citing nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json, sys
            argv = sys.argv[1:]
            sys.stdin.read()
            told = argv[argv.index("--append-system-prompt") + 1]
            model = argv[argv.index("--model") + 1] if "--model" in argv else "default"
            print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": f"[{model}] {told}"}))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return script


@pytest.fixture
def verdicts(tmp_path: Path) -> Path:
    path = tmp_path / "verdicts.json"
    verdict = {"correct": True, "reasoning": "Fine.", "relevant_citations": []}
    path.write_text(json.dumps({"day-of-week": verdict, "swim-instructor": verdict}))
    return path


def test_the_claude_runner_asks_each_question_at_its_as_of_time(
    tmp_path: Path, fake_claude: Path, verdicts: Path
):
    runs = tmp_path / "runs"

    exit_code = main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--only", "swim-instructor"),
            *("--agent", "claude", "--agent-model", "sonnet"),
            *("--judge", "scripted", "--verdicts", str(verdicts)),
        ]
    )

    assert exit_code == 0
    [run_dir] = runs.iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    answers = {q["id"]: q["answer"] for q in results["questions"]}
    assert answers["day-of-week"].startswith("[sonnet] ")
    assert "Saturday, October 10, 2026, 09:00" in answers["day-of-week"]
    assert "Tuesday, October 6, 2026, 20:00" in answers["swim-instructor"]
    assert {k: v for k, v in results["setup"].items() if k != "commit"} == {
        "agent": "claude",
        "agent model": "sonnet",
        "as-of method": "system-prompt",
        "why this as-of method": WHY_SYSTEM_PROMPT,
        "as-of methods compared in": "docs/adr/0003-as-of-time-injection.md",
        "judge": "scripted",
    }
    report = (run_dir / "report.md").read_text()
    assert "- As-of method: system-prompt" in report
    assert f"- Why this as-of method: {WHY_SYSTEM_PROMPT}" in report
    assert "- As-of methods compared in: docs/adr/0003-as-of-time-injection.md" in report


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_the_day_of_week_canary_answers_with_the_as_of_date(tmp_path: Path):
    runs = tmp_path / "runs"
    model = os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku")

    main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--agent", "claude", "--agent-model", model),
            *("--judge", "model", "--judge-model", model),
        ]
    )

    [run_dir] = runs.iterdir()
    [question] = json.loads((run_dir / "results.json").read_text())["questions"]
    assert question["status"] == "scored", question["error"]
    assert "Saturday" in question["answer"]
    assert question["correct"] is True, question["judge_reasoning"]
