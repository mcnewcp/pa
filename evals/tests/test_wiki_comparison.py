"""`pa-eval compare-wikis`: one eval wiki against another over a cut, as a public report of
numbers and a private failure analysis (#140, the real-data comparison)."""

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
"""A fictional cut."""

QUESTION = "When does Juniper's quilting class move to Thursdays?"
EXPECTED = "From Dec 4, at the Elm Street library."
ANSWER = "It moves on Dec 11, per the flyer."
REASONING = "Wrong date: the email says Dec 4."
FOUND = "gmail_5a1e0c0ffee0b0b0"
NEVER_FOUND = "obsidian_d00dfeedfacecafe"
PRIVATE = (QUESTION, EXPECTED, ANSWER, REASONING, FOUND, NEVER_FOUND, "r01", "r02")
"""What only the private analysis may hold."""

BUILT, DREAMED = "0d5e1a2b3c4d", "7f6e5d4c3b2a"
UNDREAMED_WIKI = WikiResult(librarian_version=BUILT)
DREAMED_WIKI = WikiResult(librarian_version=BUILT, dreaming_version=DREAMED)


def answer(question_id: str, category: Category, *, correct: bool) -> QuestionResult:
    """An answer citing one of its two evidence episodes, the other never opened."""
    return QuestionResult(
        id=question_id,
        category=category,
        question=QUESTION,
        as_of=AS_OF,
        expected_answer=EXPECTED,
        evidence=[FOUND, NEVER_FOUND],
        status="scored",
        answer=ANSWER,
        cited=[FOUND],
        evidence_recall=0.5,
        citation_validity=1.0,
        correct=correct,
        judge_reasoning=REASONING,
        cost_usd=0.10,
        turns=4,
        duration_ms=12_000,
        tool_calls=[],
        cited_opened=[FOUND],
        evidence_opened=[FOUND],
    )


def runs(
    wiki: WikiResult | None,
    correct: Sequence[bool],
    *,
    configuration: str = "files-plus-wiki",
    cut: CutResult | None = CUT,
) -> list[RunResults]:
    """One run per entry of `correct`, each of two questions: r01 answered `correct[n]` in run
    n, r02 always right."""
    made = []
    for right in correct:
        questions = [
            answer("r01", "open_loops", correct=right),
            answer("r02", "single_fact_recall", correct=True),
        ]
        made.append(
            RunResults(
                configuration=configuration,
                setup={"commit": "4377b4857b17", "agent model": "sonnet"},
                wiki=wiki,
                as_of=AS_OF,
                cut=cut,
                overall=Scores.over(questions),
                categories={q.category: Scores.over([q]) for q in questions},
                questions=questions,
            )
        )
    return made


def compare(
    tmp_path: Path,
    first: Sequence[RunResults],
    second: Sequence[RunResults],
    *,
    paths: dict[str, Path] | None = None,
) -> tuple[int, str, str]:
    """Run `pa-eval compare-wikis`, returning its exit code, the public report, and the
    private analysis. `paths` gives options a path in place of the usual one."""
    real = tmp_path / "data-root" / "evals"
    options = {
        "--first": real / "runs" / "first",
        "--second": real / "runs" / "second",
        "--public": tmp_path / "comparison.md",
        "--private": real / "comparison-private.md",
    }
    for option, written in (("--first", first), ("--second", second)):
        for number, run in enumerate(written, start=1):
            (options[option] / f"run-{number}").mkdir(parents=True)
            (options[option] / f"run-{number}" / "results.json").write_text(run.model_dump_json())
    options.update(paths or {})
    code = main(["compare-wikis", *(str(item) for option in options.items() for item in option)])
    read = lambda path: path.read_text() if path.exists() else ""  # noqa: E731
    return code, read(options["--public"]), read(options["--private"])


def test_the_public_comparison_holds_numbers_only_and_says_whether_the_second_wiki_passes(
    tmp_path: Path,
):
    code, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [True, False, True]), runs(DREAMED_WIKI, [True] * 3)
    )

    assert code == 0
    for text in PRIVATE:
        assert text not in public, text
    # Which wiki each set of runs was given, the cut, and how many runs.
    assert f"librarian version {BUILT}, dreamed by librarian version {DREAMED}" in public
    assert "taken 2026-11-18 20:27 UTC, 1616 episodes" in public
    assert "59b9ce244da8" not in public
    assert "the undreamed wiki 3, the dreamed wiki 3" in public
    # Each category on both wikis, then whether the dreamed one holds up.
    assert (
        "| open_loops | 0.67 (0.00 to 1.00) | 1.00 (1.00 to 1.00) | none | 1.00 | yes |" in public
    )
    assert "**Passes**: overall correctness is 1.00, at least 0.83" in public
    assert "| Evidence recall | 0.50 (0.50 to 0.50) | 0.50 (0.50 to 0.50) |" in public


def test_a_second_wiki_that_does_worse_fails_the_comparison(tmp_path: Path):
    code, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [True] * 3), runs(DREAMED_WIKI, [True, False, False])
    )

    assert code == 0
    assert "**Fails**: overall correctness is 0.67, below 1.00." in public
    assert "**Fails**: open_loops drops by 0.67, more than the spread of 0.00." in public
    assert "**Passes**" not in public


def test_the_private_analysis_holds_each_question_run_by_run_and_each_failed_answer(
    tmp_path: Path,
):
    _, _, private = compare(
        tmp_path, runs(UNDREAMED_WIKI, [True] * 3), runs(DREAMED_WIKI, [True, False, True])
    )

    assert "| r01 | open_loops | yes, yes, yes | yes, no, yes |" in private
    assert "## the dreamed wiki, run 2: r01 (open_loops)" in private
    for text in (QUESTION, EXPECTED, ANSWER, REASONING):
        assert text in private
    assert f"`{NEVER_FOUND}` (never opened)" in private
    assert "## the undreamed wiki, run" not in private  # it failed nothing
    assert (tmp_path / "data-root/evals/comparison-private.md").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("option", ["--first", "--second", "--private"])
def test_real_data_inside_the_repository_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str
):
    inside = REPO / "evals" / "runs" / "comparison-inside-the-repository"

    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, [True] * 3),
        runs(DREAMED_WIKI, [True] * 3),
        paths={option: inside},
    )

    assert code == 1
    assert option in capsys.readouterr().err
    assert not inside.exists() and public == ""


REFUSED = {
    "fewer than three runs": (
        runs(UNDREAMED_WIKI, [True] * 3),
        runs(DREAMED_WIKI, [True] * 2),
        "--second holds 2 runs; a comparison needs 3 runs of each wiki",
    ),
    "runs without a wiki": (
        runs(None, [True] * 3, configuration="files-alone"),
        runs(DREAMED_WIKI, [True] * 3),
        "--first holds runs of files-alone, not files-plus-wiki",
    ),
    "the same wiki twice": (
        runs(DREAMED_WIKI, [True] * 3),
        runs(DREAMED_WIKI, [True] * 3),
        "both sets of runs were given the same eval wiki",
    ),
    "a set given two wikis": (
        [*runs(UNDREAMED_WIKI, [True] * 2), *runs(DREAMED_WIKI, [True])],
        runs(DREAMED_WIKI, [True] * 3),
        "--first holds runs given more than one eval wiki",
    ),
    "runs over a corpus": (
        runs(UNDREAMED_WIKI, [True] * 3, cut=None),
        runs(DREAMED_WIKI, [True] * 3),
        "--first holds a run that wasn't asked over a cut",
    ),
    "two cuts": (
        runs(UNDREAMED_WIKI, [True] * 3),
        runs(DREAMED_WIKI, [True] * 3, cut=CUT.model_copy(update={"name": "20261119T000000Z"})),
        "the runs weren't all asked over the same cut",
    ),
}


@pytest.mark.parametrize("refused", REFUSED, ids=list(REFUSED))
def test_runs_that_dont_compare_two_wikis_over_one_cut_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], refused: str
):
    first, second, reason = REFUSED[refused]

    code, public, private = compare(tmp_path, first, second)

    assert code == 1
    assert reason in capsys.readouterr().err
    assert (public, private) == ("", "")
