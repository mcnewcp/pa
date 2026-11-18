"""`pa-eval verdict`: the real-data verdict (#100), over synthetic run directories.

The public verdict, committed to the repository, holds numbers only: no question, expected
answer, answer, reasoning, or id from the real-data eval set. The private verdict, under the data
root, holds the failure analysis.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_evals.cli import main
from pa_evals.eval_set import Category
from pa_evals.results import CutResult, QuestionResult, RunResults, Scores, WikiResult

REPO = Path(__file__).resolve().parents[2]
AS_OF = datetime(2026, 11, 18, 20, 27, 37, tzinfo=UTC)
CUT = CutResult(name="20261118T202737Z", as_of=AS_OF, episodes=1616, wiki_commit="59b9ce244da8")

# Distinctive text that stands for the real-data eval set's: none of it may reach the public
# verdict.
QUESTION = "When does Juniper's quilting class move to Thursdays?"
EXPECTED = "From Dec 4, at the Elm Street library."
ANSWER = "It moves on Dec 11, per the flyer."
REASONING = "Wrong date: the email says Dec 4."
FOUND = "gmail_5a1e0c0ffee0b0b0"
NEVER_FOUND = "obsidian_d00dfeedfacecafe"
PRIVATE = (QUESTION, EXPECTED, ANSWER, REASONING, FOUND, NEVER_FOUND, "r01", "r02")
# A run's setup holds prose besides its settings: the public verdict shows the settings alone.
SETUP_PROSE = "told twice, it was the one method to pass every canary"


def answer(
    question_id: str,
    category: Category,
    *,
    correct: bool,
    recall: float = 1.0,
    opened: Sequence[str] | None = (FOUND,),
    cost: float = 0.10,
) -> QuestionResult:
    """An answer with `recall` of its two evidence episodes cited (1.0: both; 0.5: FOUND
    only), of which the session `opened` those given (None: its tool calls weren't
    recorded)."""
    cited = [FOUND, NEVER_FOUND] if recall == 1.0 else [FOUND]
    return QuestionResult(
        id=question_id,
        category=category,
        question=QUESTION,
        as_of=AS_OF,
        expected_answer=EXPECTED,
        evidence=[FOUND, NEVER_FOUND],
        status="scored",
        answer=ANSWER,
        cited=cited,
        evidence_recall=recall,
        citation_validity=1.0,
        correct=correct,
        judge_reasoning=REASONING,
        cost_usd=cost,
        turns=4,
        duration_ms=12_000,
        tool_calls=[],
        cited_opened=None if opened is None else [i for i in cited if i in opened],
        evidence_opened=None if opened is None else list(opened),
    )


def a_run(configuration: str, questions: list[QuestionResult], cut: CutResult = CUT) -> RunResults:
    categories: dict[Category, Scores] = {}
    for question in questions:
        category = question.category
        categories[category] = Scores.over([q for q in questions if q.category == category])
    return RunResults(
        configuration=configuration,
        setup={
            "commit": "4377b4857b17",
            "cut": cut.name,
            "agent": "claude",
            "agent model": "sonnet",
            "as-of method": "both",
            "why this as-of method": SETUP_PROSE,
            "judge": "model",
            "judge model": "sonnet",
        },
        wiki=WikiResult(librarian_version="0d5e1a2b3c4d") if "wiki" in configuration else None,
        as_of=AS_OF,
        cut=cut,
        overall=Scores.over(questions),
        categories=categories,
        questions=questions,
    )


def write(run_dir: Path, runs: Sequence[RunResults]) -> Path:
    for number, run in enumerate(runs, start=1):
        (run_dir / f"run-{number}").mkdir(parents=True)
        (run_dir / f"run-{number}" / "results.json").write_text(run.model_dump_json())
    return run_dir


def runs(
    configuration: str, correct: Sequence[bool], recall: float = 1.0, **kw
) -> list[RunResults]:
    """Three runs of two questions: r01 answered `correct[n]` in run n, r02 always right."""
    return [
        a_run(
            configuration,
            [
                answer("r01", "open_loops", correct=right, recall=recall, **kw),
                answer("r02", "single_fact_recall", correct=True, recall=recall, **kw),
            ],
        )
        for right in correct
    ]


@pytest.fixture
def scale(tmp_path: Path) -> dict[str, Path]:
    """The scale corpus's runs, recall 1.0 in each configuration (no spread)."""
    return {
        "files-alone": write(tmp_path / "scale-fa", runs("files-alone", [True] * 3)),
        "files-plus-wiki": write(tmp_path / "scale-fw", runs("files-plus-wiki", [True] * 3)),
    }


def verdict(
    tmp_path: Path,
    scale: dict[str, Path],
    files_alone: list[RunResults],
    files_plus_wiki: list[RunResults],
    *,
    paths: dict[str, Path] | None = None,
) -> tuple[int, str, str]:
    """Run `pa-eval verdict`, returning its exit code, the public and the private verdict.
    `paths` gives options a path in place of the one their runs are written to."""
    real = tmp_path / "data-root" / "evals"
    options = {
        "--files-alone": write(real / "runs" / "fa", files_alone),
        "--files-plus-wiki": write(real / "runs" / "fw", files_plus_wiki),
        "--scale-files-alone": scale["files-alone"],
        "--scale-files-plus-wiki": scale["files-plus-wiki"],
        "--public": tmp_path / "verdict.md",
        "--private": real / "verdict-private.md",
        **(paths or {}),
    }
    code = main(["verdict", *(str(item) for option in options.items() for item in option)])
    public_path, private_path = options["--public"], options["--private"]
    read = lambda path: path.read_text() if path.exists() else ""  # noqa: E731
    return code, read(public_path), read(private_path)


def test_the_public_verdict_holds_numbers_only(tmp_path: Path, scale: dict[str, Path]):
    code, public, _ = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True, False, False]),
        runs("files-plus-wiki", [True, True, False]),
    )

    assert code == 0
    for text in PRIVATE:
        assert text not in public
    assert "| open_loops (1) | 0.33 (0.00 to 1.00) | 0.67 (0.00 to 1.00) | +0.33 |" in public
    assert "| Overall (2) | 0.67 (0.50 to 1.00) | 0.83 (0.50 to 1.00) | +0.17 |" in public
    assert "| Evidence recall | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |" in public
    assert "| Citation validity | 1.00 (1.00 to 1.00) | 1.00 (1.00 to 1.00) |" in public
    assert "| Total cost | $0.60 | $0.60 |" in public
    assert "1616 episodes" in public


def test_the_public_verdict_names_the_live_wiki_but_not_its_commit(
    tmp_path: Path, scale: dict[str, Path]
):
    """The live wiki's history stays unprinted, its commits included."""
    _, public, _ = verdict(
        tmp_path, scale, runs("files-alone", [True] * 3), runs("files-plus-wiki", [True] * 3)
    )

    assert "the live wiki" in public
    assert CUT.wiki_commit is not None and CUT.wiki_commit not in public


def test_the_public_verdict_shows_the_runs_settings_and_no_other_setup(
    tmp_path: Path, scale: dict[str, Path]
):
    _, public, _ = verdict(
        tmp_path, scale, runs("files-alone", [True] * 3), runs("files-plus-wiki", [True] * 3)
    )

    for line in (
        "  - Agent: claude",
        "  - Agent model: sonnet",
        "  - Judge: model",
        "  - Judge model: sonnet",
        "  - Commit: 4377b4857b17",
        "  - As-of method: both",
    ):
        assert line in public.splitlines()
    assert SETUP_PROSE not in public


def test_the_private_verdict_holds_each_failed_answer_and_what_its_session_never_opened(
    tmp_path: Path, scale: dict[str, Path]
):
    _, _, private = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True, False, True], recall=0.5),
        runs("files-plus-wiki", [True, True, True]),
    )

    for text in (QUESTION, EXPECTED, ANSWER, REASONING):
        assert text in private
    assert "files-alone, run 2" in private
    assert f"`{NEVER_FOUND}` (never opened)" in private
    assert "files-plus-wiki, run" not in private  # it failed nothing


@pytest.mark.parametrize("option", ["--files-alone", "--files-plus-wiki", "--private"])
def test_real_data_inside_the_repository_is_refused(
    tmp_path: Path, scale: dict[str, Path], capsys: pytest.CaptureFixture[str], option: str
):
    """The real runs and the private verdict are real data: none is read or written in the
    repository."""
    inside = REPO / "evals" / "runs" / "verdict-inside-the-repository"

    code, public, _ = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True] * 3),
        runs("files-plus-wiki", [True] * 3),
        paths={option: inside},
    )

    assert code == 1
    assert option in capsys.readouterr().err
    assert not inside.exists() and public == ""


@pytest.mark.parametrize(
    ("files_alone", "files_plus_wiki", "refused"),
    [(2, 3, "--files-alone"), (3, 2, "--files-plus-wiki")],
)
def test_a_verdict_over_fewer_than_three_runs_of_either_configuration_is_refused(
    tmp_path: Path,
    scale: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    files_alone: int,
    files_plus_wiki: int,
    refused: str,
):
    """#100 asks each configuration over 3 runs, so a difference can be told from noise."""
    code, public, private = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True] * files_alone),
        runs("files-plus-wiki", [True] * files_plus_wiki),
    )

    assert code == 1
    error = capsys.readouterr().err
    assert refused in error and "3 runs" in error
    assert public == "" and private == ""


@pytest.mark.parametrize(
    ("files_alone", "files_plus_wiki", "outcome"),
    [
        ([True, False, False], [True, True, False], "**Met.**"),
        ([True, True, False], [True, True, False], "**Met.**"),
        ([True, True, False], [True, False, False], "**Not met.**"),
    ],
)
def test_the_prototype_is_met_when_files_plus_wiki_scores_at_least_as_well_overall(
    tmp_path: Path,
    scale: dict[str, Path],
    files_alone: list[bool],
    files_plus_wiki: list[bool],
    outcome: str,
):
    _, public, _ = verdict(
        tmp_path, scale, runs("files-alone", files_alone), runs("files-plus-wiki", files_plus_wiki)
    )

    [line] = [line for line in public.splitlines() if line.startswith(("**Met.", "**Not met."))]
    assert line.startswith(outcome)


@pytest.mark.parametrize(
    ("recall", "opened", "needed"),
    [
        # As high as the scale corpus's: not needed.
        (1.0, (FOUND,), False),
        # Lower by more than the spread, but the sessions opened the evidence they missed.
        (0.5, (FOUND, NEVER_FOUND), False),
        # Lower by more than the spread, and the evidence missed was never opened.
        (0.5, (FOUND,), True),
    ],
)
def test_search_is_needed_only_when_recall_is_lower_and_the_missed_evidence_was_never_found(
    tmp_path: Path, scale: dict[str, Path], recall: float, opened: tuple[str, ...], needed: bool
):
    _, public, _ = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True] * 3, recall=recall, opened=opened),
        runs("files-plus-wiki", [True] * 3, recall=recall, opened=opened),
    )

    expected = "**Needed:" if needed else "**Not needed:"
    assert expected in public


def test_search_cant_be_decided_when_recall_is_lower_but_no_session_recorded_what_it_opened(
    tmp_path: Path, scale: dict[str, Path]
):
    _, public, _ = verdict(
        tmp_path,
        scale,
        runs("files-alone", [True] * 3, recall=0.5, opened=None),
        runs("files-plus-wiki", [True] * 3),
    )

    assert "**Can't be decided:**" in public
    assert "**Not needed:" not in public and "**Needed:" not in public


def test_runs_over_different_cuts_are_refused(
    tmp_path: Path, scale: dict[str, Path], capsys: pytest.CaptureFixture[str]
):
    other = CUT.model_copy(update={"name": "20261119T000000Z"})
    wiki = [
        a_run("files-plus-wiki", r.questions, cut=other)
        for r in runs("files-plus-wiki", [True] * 3)
    ]

    code, public, _ = verdict(tmp_path, scale, runs("files-alone", [True] * 3), wiki)

    assert code == 1
    assert "same cut" in capsys.readouterr().err
    assert public == ""


def test_a_run_directory_of_the_wrong_configuration_is_refused(
    tmp_path: Path, scale: dict[str, Path], capsys: pytest.CaptureFixture[str]
):
    code, _, _ = verdict(
        tmp_path, scale, runs("files-plus-wiki", [True] * 3), runs("files-plus-wiki", [True] * 3)
    )

    assert code == 1
    assert "--files-alone" in capsys.readouterr().err
