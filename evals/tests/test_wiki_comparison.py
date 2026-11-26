"""`pa-eval compare-wikis`: one eval wiki against another over a cut, as a public report of
numbers and a private failure analysis (#140, the real-data comparison)."""

from collections.abc import Mapping, Sequence
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


CATEGORIES: dict[str, Category] = {"r01": "open_loops"}
"""Each question's category, where it isn't single-fact recall."""


def answer(question_id: str, *, correct: bool | None, tier: int | None = None) -> QuestionResult:
    """An answer citing one of its two evidence episodes, the other never opened. Or, with
    `correct` None, a question whose agent failed, as the harness records one."""
    if correct is None:
        return QuestionResult(
            id=question_id,
            category=CATEGORIES.get(question_id, "single_fact_recall"),
            tier=tier,
            question=QUESTION,
            as_of=AS_OF,
            expected_answer=EXPECTED,
            evidence=[FOUND, NEVER_FOUND],
            status="failed",
            error="agent: the session ended without an answer",
            evidence_recall=0.0,
        )
    return QuestionResult(
        id=question_id,
        category=CATEGORIES.get(question_id, "single_fact_recall"),
        tier=tier,
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
    correct: Sequence[bool | None] | Mapping[str, Sequence[bool | None]],
    *,
    tiers: Mapping[str, int] | None = None,
    configuration: str = "files-plus-wiki",
    cut: CutResult | None = CUT,
) -> list[RunResults]:
    """One run per entry of `correct`, each of two questions: r01 (open loops) answered
    `correct[n]` in run n, r02 (single-fact recall) always right. Or, given each question's
    outcomes run by run, a run of those questions, each in the tier `tiers` gives it. An
    outcome of None (`FAILED`) is a question whose agent failed in that run."""
    if not isinstance(correct, Mapping):
        correct = {"r01": correct, "r02": [True] * len(correct)}
    made = []
    for number in range(len(next(iter(correct.values())))):
        questions = [
            answer(question_id, correct=right[number], tier=(tiers or {}).get(question_id))
            for question_id, right in correct.items()
        ]
        made.append(
            RunResults(
                configuration=configuration,
                setup={"commit": "4377b4857b17", "agent model": "sonnet"},
                wiki=wiki,
                as_of=AS_OF,
                cut=cut,
                overall=Scores.over(questions),
                categories={
                    q.category: Scores.over([a for a in questions if a.category == q.category])
                    for q in questions
                },
                tiers={
                    tier: Scores.over([q for q in questions if q.tier == tier])
                    for tier in sorted({q.tier for q in questions if q.tier is not None})
                },
                questions=questions,
            )
        )
    return made


Y, N, FAILED = True, False, None


def compare(
    tmp_path: Path,
    first: Sequence[RunResults],
    second: Sequence[RunResults],
    *,
    first_again: Sequence[RunResults] = (),
    paths: dict[str, Path] | None = None,
) -> tuple[int, str, str]:
    """Run `pa-eval compare-wikis`, returning its exit code, the public report, and the
    private analysis. `first_again` is a second run directory for `--first`; `paths` gives
    options a path in place of the usual one."""
    real = tmp_path / "data-root" / "evals"
    options = {
        "--first": real / "runs" / "first",
        "--second": real / "runs" / "second",
        "--public": tmp_path / "comparison.md",
        "--private": real / "comparison-private.md",
    }
    again = real / "runs" / "first-again"
    for run_dir, written in ((options["--first"], first), (options["--second"], second)):
        lay_runs(run_dir, written)
    lay_runs(again, first_again)
    options.update(paths or {})
    args = [str(item) for option in options.items() for item in option]
    if first_again:
        args.insert(2, str(again))
    code = main(["compare-wikis", *args])
    read = lambda path: path.read_text() if path.exists() else ""  # noqa: E731
    return code, read(options["--public"]), read(options["--private"])


def lay_runs(run_dir: Path, written: Sequence[RunResults]) -> None:
    """Write runs into a run directory as `pa-eval run --runs N` lays them."""
    for number, run in enumerate(written, start=1):
        (run_dir / f"run-{number}").mkdir(parents=True)
        (run_dir / f"run-{number}" / "results.json").write_text(run.model_dump_json())


def test_the_public_comparison_holds_numbers_only_and_says_whether_the_second_wiki_passes(
    tmp_path: Path,
):
    code, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y, N, Y, Y, Y, Y]), runs(DREAMED_WIKI, [Y, Y, Y])
    )

    assert code == 0
    for text in PRIVATE:
        assert text not in public, text
    # Which wiki each set of runs was given, the cut, and how many runs.
    assert f"librarian version {BUILT}, dreamed by librarian version {DREAMED}" in public
    assert "taken 2026-11-18 20:27 UTC, 1616 episodes" in public
    assert "59b9ce244da8" not in public
    assert "the undreamed wiki 6, the dreamed wiki 3" in public
    # The drop and the noise floor, overall and for each category of a set without tiers.
    # r01 is right in 5 of the undreamed wiki's 6 runs and all 3 of the dreamed wiki's, so it
    # rises by 0.167, and by half that over both questions. Whichever half of the undreamed
    # wiki's runs holds the wrong one scores 2 of 3 on it against the other half's 3 of 3.
    assert (
        "| | Questions | the undreamed wiki | the dreamed wiki | Drop | Noise floor | Within it |"
    ) in public
    assert "| Overall | 2 (few) | 0.92 | 1.00 | -0.083 | 0.167 | yes |" in public
    assert "| open_loops | 1 (few) | 0.83 | 1.00 | -0.167 | 0.333 | yes |" in public
    assert "| single_fact_recall | 1 (few) | 1.00 | 1.00 | 0.000 | 0.000 | yes |" in public
    assert (
        "**Passes**: no question fell, and the drop is no larger than the noise floor, overall "
        "(-0.083 against 0.167) and in every category."
    ) in public
    assert "**Fails**" not in public
    assert "| Evidence recall | 0.50 (0.50 to 0.50) | 0.50 (0.50 to 0.50) |" in public


def test_a_drop_larger_than_the_noise_floor_fails_the_comparison(tmp_path: Path):
    # The undreamed wiki's runs never differ, so its floor is 0, and r01's two wrong answers
    # are a drop of more than one.
    code, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y] * 6), runs(DREAMED_WIKI, [Y, N, N])
    )

    assert code == 0
    assert "| Overall | 2 (few) | 1.00 | 0.67 | 0.333 | 0.000 | no |" in public
    assert "| open_loops | 1 (few) | 1.00 | 0.33 | 0.667 | 0.000 | no |" in public
    assert (
        "**Fails**: overall drops by 0.333, more than the noise floor of 0.000 and the "
        "one-answer allowance of 0.167."
    ) in public
    assert (
        "**Fails**: open_loops drops by 0.667, more than the noise floor of 0.000 and the "
        "one-answer allowance of 0.333."
    ) in public
    assert "**Passes**" not in public


def test_a_drop_inside_the_noise_floor_passes_the_comparison(tmp_path: Path):
    # r01 falls from 5 of 6 to 2 of 3, by 0.167: no further than the undreamed wiki's own
    # halves fall from each other, 0.333 (3 of 3 against 2 of 3).
    code, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y, N, Y, Y, Y, Y]), runs(DREAMED_WIKI, [Y, Y, N])
    )

    assert code == 0
    assert "| Overall | 2 (few) | 0.92 | 0.83 | 0.083 | 0.167 | yes |" in public
    assert "| open_loops | 1 (few) | 0.83 | 0.67 | 0.167 | 0.333 | yes |" in public
    assert (
        "**Passes**: no question fell, and the drop is no larger than the noise floor, overall "
        "(0.083 against 0.167) and in every category."
    ) in public
    assert "**Fails**" not in public


def test_a_tier_that_drops_by_more_than_its_noise_floor_fails_though_overall_holds(
    tmp_path: Path,
):
    # r01 (tier 1) falls from 6 of 6 to 1 of 3 and r02 (tier 0) rises from 4 of 6 to 3 of 3,
    # so overall falls by 0.167. The undreamed wiki's halves differ only on r02, 1 of 3
    # against 3 of 3, which is 0.333 overall.
    tiers = {"r01": 1, "r02": 0}
    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {"r01": [Y] * 6, "r02": [N, N, Y, Y, Y, Y]}, tiers=tiers),
        runs(DREAMED_WIKI, {"r01": [Y, N, N], "r02": [Y, Y, Y]}, tiers=tiers),
    )

    assert code == 0
    assert "overall and in every tier." in public
    assert "| Overall | 2 (few) | 0.83 | 0.67 | 0.167 | 0.333 | yes |" in public
    assert "| Tier 0 | 1 (few) | 0.67 | 1.00 | -0.333 | 0.667 | yes |" in public
    assert "| Tier 1 | 1 (few) | 1.00 | 0.33 | 0.667 | 0.000 | no |" in public
    assert (
        "**Fails**: tier 1 drops by 0.667, more than the noise floor of 0.000 and the "
        "one-answer allowance of 0.333."
    ) in public
    assert "**Fails**: overall" not in public and "**Passes**" not in public


def test_with_tiers_the_categories_are_reported_beside_them_and_decide_nothing(tmp_path: Path):
    # Open loops, which is r01 alone, falls by 0.333 with no floor to fall within. Its tier
    # holds r02 as well, and falls no further than the undreamed wiki's own halves.
    tiers = {"r01": 0, "r02": 0}
    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {"r01": [Y] * 6, "r02": [N, N, Y, Y, Y, Y]}, tiers=tiers),
        runs(DREAMED_WIKI, {"r01": [Y, Y, N], "r02": [Y, Y, Y]}, tiers=tiers),
    )

    assert code == 0
    assert "| Tier 0 | 2 (few) | 0.83 | 0.83 | 0.000 | 0.333 | yes |" in public
    by_category = public[public.index("### By category") :].split("\n## ")[0]
    assert "its categories decide nothing" in by_category
    assert (
        "| Category | Questions | the undreamed wiki | the dreamed wiki | Drop | Noise floor |"
        in (by_category)
    )
    assert "| open_loops | 1 | 1.00 | 0.67 | 0.333 | 0.000 |" in by_category
    assert "| single_fact_recall | 1 | 0.67 | 1.00 | -0.333 | 0.667 |" in by_category
    assert (
        "**Passes**: no question fell, and the drop is no larger than the noise floor, overall "
        "(0.000 against 0.333) and in every tier."
    ) in public
    assert "**Fails**" not in public


FALLEN_TIERS = {"r01": 5, "r02": 5, "r03": 5, "r04": 0}
FALLEN_FIRST = {
    "r01": [Y] * 6,
    "r02": [N, N, N, Y, Y, Y],
    "r03": [N, N, N, Y, Y, Y],
    "r04": [Y] * 6,
}
FALLEN_SECOND = {"r01": [N] * 3, "r02": [Y] * 3, "r03": [Y] * 3, "r04": [Y] * 3}
"""A fallen question hidden in its tier's mean (#171): r01 is right in all 6 runs on the
undreamed wiki and wrong in all 3 on the dreamed one. The rest of tier 5, r02 and r03, are each
right in 3 of 6 and then 3 of 3, so the tier scores 12 of 18 and then 6 of 9, no drop at all."""


def test_a_question_that_falls_in_every_run_fails_the_comparison_though_its_tier_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, FALLEN_FIRST, tiers=FALLEN_TIERS),
        runs(DREAMED_WIKI, FALLEN_SECOND, tiers=FALLEN_TIERS),
    )

    assert code == 0
    # No group drops: the undreamed wiki's own halves fall 0.667 from each other in tier 5.
    assert "| Overall | 4 (few) | 0.75 | 0.75 | 0.000 | 0.500 | yes |" in public
    assert "| Tier 0 | 1 (few) | 1.00 | 1.00 | 0.000 | 0.000 | yes |" in public
    assert "| Tier 5 | 3 (few) | 0.67 | 0.67 | 0.000 | 0.667 | yes |" in public
    assert (
        "- **A fallen question** is one that every run on the undreamed wiki got right and "
        "every run on the dreamed wiki got wrong."
    ) in public
    # The public report counts it, by tier, and doesn't name it.
    assert "**Fails**: 1 question fell: 1 in tier 5." in public
    assert "**Passes**" not in public and "drops by" not in public
    assert "r01" not in public
    assert "compare-wikis: the dreamed wiki fails;" in capsys.readouterr().out


def test_the_public_report_counts_fallen_questions_by_tier_lowest_first(tmp_path: Path):
    tiers = {"r01": 9, "r02": 5, "r03": 5, "r04": 0}
    _, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {question: [Y] * 6 for question in tiers}, tiers=tiers),
        runs(
            DREAMED_WIKI,
            {"r01": [N] * 3, "r02": [N] * 3, "r03": [N] * 3, "r04": [Y] * 3},
            tiers=tiers,
        ),
    )

    assert "**Fails**: 3 questions fell: 2 in tier 5, 1 in tier 9." in public
    # Beside the tiers they took beyond the floor.
    assert "**Fails**: tier 5 drops by 1.000, more than the noise floor of 0.000" in public
    for question in tiers:
        assert question not in public


def test_without_tiers_the_public_report_counts_fallen_questions_by_category(tmp_path: Path):
    _, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {"r01": [Y] * 6, "r02": [N, N, N, Y, Y, Y]}),
        runs(DREAMED_WIKI, {"r01": [N] * 3, "r02": [Y] * 3}),
    )

    assert "**Fails**: 1 question fell: 1 in open_loops." in public


def test_the_private_analysis_names_the_fallen_questions_first(tmp_path: Path):
    _, _, private = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, FALLEN_FIRST, tiers=FALLEN_TIERS),
        runs(DREAMED_WIKI, FALLEN_SECOND, tiers=FALLEN_TIERS),
    )

    sections = private.split("\n## ")
    assert sections[1].startswith("The fallen questions\n")
    assert (
        "Right in every run on the undreamed wiki and wrong in every run on the dreamed wiki. "
        "Any fails the comparison."
    ) in sections[1]
    assert "| Question | Category | Tier |" in sections[1]
    assert "| r01 | open_loops | 5 |" in sections[1]
    # r02 and r03 rose, and r04 held.
    assert not any(question in sections[1] for question in ("r02", "r03", "r04"))
    assert sections[2].startswith("The questions whose score fell\n")


def test_the_private_analysis_says_when_no_question_fell_in_every_run(tmp_path: Path):
    # r01 falls from 6 of 6 to 1 of 3: a long way, and not in every run.
    _, public, private = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y] * 6), runs(DREAMED_WIKI, [N, Y, N])
    )

    fallen = private[private.index("## The fallen questions") :].split("\n## ")[0]
    assert "None." in fallen and "r01" not in fallen
    assert " fell: " not in public


CUT_SHORT_TIERS = {f"r{number:02}": 0 for number in range(1, 7)}
# Comparisons in which an answer failed to run: each wiki's outcomes, the tiers, what the
# reports count, and a row of the public report's table.
NOT_JUDGED = {
    # A fallen question, had its one failed answer run and been right: failed, it isn't right
    # in every run on the undreamed wiki, and its halves fall 0.333 from each other.
    "an answer on the first wiki": (
        {"r01": [Y, Y, FAILED, Y, Y, Y], "r02": [Y] * 6},
        {"r01": [N] * 3, "r02": [Y] * 3},
        None,
        "1 of the undreamed wiki's 12 answers failed to run and 0 of the dreamed wiki's 6",
        "| open_loops | 1 (few) | 0.83 | 0.00 | 0.833 | 0.333 | no |",
    ),
    "an answer on the second wiki": (
        {"r01": [Y] * 6, "r02": [Y] * 6},
        {"r01": [N, FAILED, N], "r02": [Y] * 3},
        None,
        "0 of the undreamed wiki's 12 answers failed to run and 1 of the dreamed wiki's 6",
        "| open_loops | 1 (few) | 1.00 | 0.00 | 1.000 | 0.000 | no |",
    ),
    # A whole run of the undreamed wiki's cut short, as by a usage limit. It widens that
    # wiki's noise floor to 0.333, which the dreamed wiki's drop of 5 answers in 18 is within,
    # and keeps r01, wrong in every run on the dreamed wiki, from counting as fallen.
    "a run cut short on the first wiki": (
        {question: [Y, Y, FAILED, Y, Y, Y] for question in CUT_SHORT_TIERS},
        {**{question: [Y] * 3 for question in CUT_SHORT_TIERS}, "r01": [N] * 3, "r02": [N, N, Y]},
        CUT_SHORT_TIERS,
        "6 of the undreamed wiki's 36 answers failed to run and 0 of the dreamed wiki's 18",
        "| Tier 0 | 6 | 0.83 | 0.72 | 0.111 | 0.333 | yes |",
    ),
}


@pytest.mark.parametrize("failed", NOT_JUDGED, ids=list(NOT_JUDGED))
def test_a_comparison_holding_an_answer_that_failed_to_run_isnt_judged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], failed: str
):
    first, second, tiers, counted, row = NOT_JUDGED[failed]

    code, public, private = compare(
        tmp_path, runs(UNDREAMED_WIKI, first, tiers=tiers), runs(DREAMED_WIKI, second, tiers=tiers)
    )

    assert code == 0
    assert (
        f"**Not judged**: {counted}. An answer that failed to run is neither right nor wrong, so "
        "those runs need running again."
    ) in public
    assert "**Passes**" not in public and "**Fails**" not in public
    # The tables are still there to read, and name no question.
    assert row in public
    for question in (*first, *second):
        assert question not in public
    assert (
        f"compare-wikis: the dreamed wiki isn't judged: {counted}, so those runs need running "
        "again; public "
    ) in capsys.readouterr().out
    assert f"**Not judged**: {counted}." in private.split("\n## ")[1]


def test_the_report_says_a_comparison_holding_an_answer_that_failed_to_run_isnt_judged(
    tmp_path: Path,
):
    _, public, _ = compare(tmp_path, runs(UNDREAMED_WIKI, [Y] * 6), runs(DREAMED_WIKI, [Y] * 3))

    assert (
        "- **An answer that failed to run** is neither right nor wrong: a comparison holding "
        "one, on either wiki, isn't judged."
    ) in public
    assert "| Failed answers (agent or judge) | 0 | 0 |" in public
    assert "**Not judged**" not in public


def test_compare_doesnt_judge_two_wikis_when_an_answer_failed_to_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    first, second = tmp_path / "first", tmp_path / "second"
    lay_runs(first, runs(UNDREAMED_WIKI, {"r01": [Y, Y, FAILED, Y, Y, Y], "r02": [Y] * 6}))
    lay_runs(second, runs(DREAMED_WIKI, {"r01": [N] * 3, "r02": [Y] * 3}))

    assert main(["compare", str(first), str(second)]) == 0

    comparison = capsys.readouterr().out
    assert (
        f"**Not judged**: 1 of {first}'s 12 answers failed to run and 0 of {second}'s 6. An "
        "answer that failed to run is neither right nor wrong, so those runs need running again."
    ) in comparison
    assert "| open_loops | 1 (few) | 0.83 | 0.00 | 0.833 | 0.333 | no |" in comparison
    assert "**Passes**" not in comparison and "**Fails**" not in comparison


ALLOWANCE_TIERS = {f"r{number:02}": 0 if number <= 6 else 9 for number in range(1, 13)}
"""Twelve questions, r01 to r06 in tier 0 and r07 to r12 in tier 9: the dreamed wiki's 3 runs
give 18 answers in each tier."""


def all_right_but(wrong: Mapping[str, Sequence[bool | None]]) -> list[RunResults]:
    """The dreamed wiki's 3 runs of `ALLOWANCE_TIERS`, every answer right but those given."""
    outcomes = {**{question: [Y] * 3 for question in ALLOWANCE_TIERS}, **wrong}
    return runs(DREAMED_WIKI, outcomes, tiers=ALLOWANCE_TIERS)


def test_one_wrong_answer_in_a_tier_the_first_wiki_got_all_right_passes_by_the_allowance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    # The undreamed wiki's runs never differ, so every floor is 0. One wrong answer of the
    # dreamed wiki's 18 in tier 9 is a drop of 0.056, and of its 36 overall, 0.028.
    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {q: [Y] * 6 for q in ALLOWANCE_TIERS}, tiers=ALLOWANCE_TIERS),
        all_right_but({"r07": [Y, N, Y]}),
    )

    assert code == 0
    assert (
        "- **The one-answer allowance** is what one of the dreamed wiki's answers moves a "
        "group's drop by: 1 over the group's questions times the dreamed wiki's 3 runs. A drop "
        "no larger than it is within, whatever the noise floor, so one answer never fails a "
        "tier."
    ) in public
    assert (
        "- **It passes** when no question fell and the drop is no larger than the noise floor "
        "or the one-answer allowance, overall and in every tier."
    ) in public
    assert (
        "| Overall | 12 | 1.00 | 0.97 | 0.028 | 0.000 | only by the allowance (0.028) |" in public
    )
    assert "| Tier 0 | 6 | 1.00 | 1.00 | 0.000 | 0.000 | yes |" in public
    assert "| Tier 9 | 6 | 1.00 | 0.94 | 0.056 | 0.000 | only by the allowance (0.056) |" in public
    assert (
        "**Passes**: no question fell, and the drop is no larger than the noise floor or the "
        "one-answer allowance, overall and in every tier. Only by the allowance: overall, "
        "tier 9."
    ) in public
    assert "**Fails**" not in public
    assert "compare-wikis: the dreamed wiki passes;" in capsys.readouterr().out


def test_two_wrong_answers_in_a_tier_the_first_wiki_got_all_right_fail_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    code, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {q: [Y] * 6 for q in ALLOWANCE_TIERS}, tiers=ALLOWANCE_TIERS),
        all_right_but({"r07": [Y, N, Y], "r08": [Y, Y, N]}),
    )

    assert code == 0
    assert "| Overall | 12 | 1.00 | 0.94 | 0.056 | 0.000 | no |" in public
    assert "| Tier 9 | 6 | 1.00 | 0.89 | 0.111 | 0.000 | no |" in public
    assert (
        "**Fails**: tier 9 drops by 0.111, more than the noise floor of 0.000 and the "
        "one-answer allowance of 0.056."
    ) in public
    assert "**Passes**" not in public and " fell: " not in public
    assert "compare-wikis: the dreamed wiki fails;" in capsys.readouterr().out


def test_a_drop_within_the_noise_floor_isnt_marked_as_passing_by_the_allowance(tmp_path: Path):
    # r07 is wrong in one of the undreamed wiki's 6 runs, so its halves fall one answer of 18
    # from each other in tier 9. The dreamed wiki's one wrong answer there, 17 of 18 against
    # 35 of 36, is inside that floor, and the allowance isn't called on.
    first = {**{q: [Y] * 6 for q in ALLOWANCE_TIERS}, "r07": [N, Y, Y, Y, Y, Y]}
    _, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, first, tiers=ALLOWANCE_TIERS),
        all_right_but({"r08": [Y, N, Y]}),
    )

    assert "| Overall | 12 | 0.99 | 0.97 | 0.014 | 0.028 | yes |" in public
    assert "| Tier 9 | 6 | 0.97 | 0.94 | 0.028 | 0.056 | yes |" in public
    assert (
        "**Passes**: no question fell, and the drop is no larger than the noise floor, overall "
        "(0.014 against 0.028) and in every tier."
    ) in public
    assert "Only by the allowance" not in public


def test_the_report_says_how_the_first_wikis_runs_were_halved(tmp_path: Path):
    _, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y, N, Y, Y, Y, Y]), runs(DREAMED_WIKI, [Y, Y, N])
    )

    # 6 runs make 20 choices of 3, and each pair of halves is two of them.
    assert (
        "A half is 3 runs, as many as the dreamed wiki was asked, and every pair of halves that "
        "share no run is tried, each way round: 10 pairs from its 6 runs."
    ) in public


def test_a_first_wiki_asked_more_than_twice_the_seconds_runs_leaves_the_rest_out_of_each_pair(
    tmp_path: Path,
):
    # r01 is right in 6 of 7 runs, then 2 of 3. A half is still 3 runs: 35 choices of 3 from
    # 7, each with 4 choices of 3 from the 4 runs left, every pair counted twice.
    _, public, _ = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y, N, Y, Y, Y, Y, Y]), runs(DREAMED_WIKI, [Y, Y, N])
    )

    assert "the undreamed wiki 7, the dreamed wiki 3" in public
    assert "A half is 3 runs, as many as the dreamed wiki was asked" in public
    assert "70 pairs from its 7 runs, each leaving 1 of them out." in public
    assert "| Overall | 2 (few) | 0.93 | 0.83 | 0.095 | 0.167 | yes |" in public
    assert "| open_loops | 1 (few) | 0.86 | 0.67 | 0.190 | 0.333 | yes |" in public


def test_a_tier_of_fewer_than_five_questions_is_marked_and_still_counts(tmp_path: Path):
    tiers = {"r01": 1, "r02": 0, "r03": 0, "r04": 0, "r05": 0, "r06": 0}
    _, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {question: [Y] * 6 for question in tiers}, tiers=tiers),
        runs(DREAMED_WIKI, {**{q: [Y] * 3 for q in tiers}, "r01": [Y, N, N]}, tiers=tiers),
    )

    # One answer of the dreamed wiki's 3 runs on 4 questions is 1/12 of that tier's score.
    assert (
        "- **(few)** marks fewer than 5 questions. Such a tier counts like any other, but one "
        "of the dreamed wiki's answers moves its drop by 0.083 or more, so its result says "
        "little by itself."
    ) in public
    assert "| Overall | 6 | 1.00 | 0.89 | 0.111 | 0.000 | no |" in public
    assert "| Tier 0 | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |" in public
    assert "| Tier 1 | 1 (few) | 1.00 | 0.33 | 0.667 | 0.000 | no |" in public
    assert "**Fails**: tier 1 drops by 0.667, more than the noise floor of 0.000 and" in public


def test_a_set_with_five_questions_in_every_group_marks_nothing(tmp_path: Path):
    questions = ("r02", "r03", "r04", "r05", "r06")
    _, public, _ = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, {question: [Y] * 6 for question in questions}),
        runs(DREAMED_WIKI, {question: [Y] * 3 for question in questions}),
    )

    assert "| single_fact_recall | 5 | 1.00 | 1.00 | 0.000 | 0.000 | yes |" in public
    assert "(few)" not in public


def test_a_wikis_runs_can_come_from_several_run_directories(tmp_path: Path):
    # The undreamed wiki's 6 runs, asked 3 at a time.
    code, public, private = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, [Y, N, Y]),
        runs(DREAMED_WIKI, [Y, Y, N]),
        first_again=runs(UNDREAMED_WIKI, [Y, Y, Y]),
    )

    assert code == 0
    assert "the undreamed wiki 6, the dreamed wiki 3" in public
    assert "| Overall | 2 (few) | 0.92 | 0.83 | 0.083 | 0.167 | yes |" in public
    # Numbered in the order the directories were given.
    assert "| r01 | open_loops | yes, no, yes, yes, yes, yes | yes, yes, no |" in private


def test_a_run_directory_given_twice_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    first = tmp_path / "data-root" / "evals" / "runs" / "first"

    code, public, private = compare(
        tmp_path,
        runs(UNDREAMED_WIKI, [Y] * 3),
        runs(DREAMED_WIKI, [Y] * 3),
        paths={"--second": first},
    )

    assert code == 1
    assert "each run directory can be given only once" in capsys.readouterr().err
    assert (public, private) == ("", "")


def test_the_private_analysis_lists_the_questions_whose_score_fell(tmp_path: Path):
    tiers = {"r01": 1, "r02": 0, "r03": 2}
    _, public, private = compare(
        tmp_path,
        runs(
            UNDREAMED_WIKI,
            {"r01": [Y] * 6, "r02": [N, N, Y, Y, Y, Y], "r03": [Y] * 6},
            tiers=tiers,
        ),
        runs(DREAMED_WIKI, {"r01": [Y, Y, N], "r02": [Y] * 3, "r03": [N] * 3}, tiers=tiers),
    )

    fell = private[private.index("## The questions whose score fell") :].split("\n## ")[0]
    assert "| Question | Category | Tier | The undreamed wiki | The dreamed wiki | Fall |" in fell
    # The furthest fall first; r02 rose, so it isn't listed.
    assert fell.index("| r03 | single_fact_recall | 2 | 1.00 | 0.00 | 1.00 |") < fell.index(
        "| r01 | open_loops | 1 | 1.00 | 0.67 | 0.33 |"
    )
    assert "r02" not in fell
    assert "whose score fell" not in public


def test_the_private_analysis_says_when_no_questions_score_fell(tmp_path: Path):
    _, _, private = compare(
        tmp_path, runs(UNDREAMED_WIKI, [Y, N, Y, Y, Y, Y]), runs(DREAMED_WIKI, [Y] * 3)
    )

    fell = private[private.index("## The questions whose score fell") :].split("\n## ")[0]
    assert "None fell." in fell
    assert "| r01 |" not in fell


def test_the_private_analysis_holds_each_question_run_by_run_and_each_failed_answer(
    tmp_path: Path,
):
    _, _, private = compare(tmp_path, runs(UNDREAMED_WIKI, [Y] * 6), runs(DREAMED_WIKI, [Y, N, Y]))

    assert "| r01 | open_loops | yes, yes, yes, yes, yes, yes | yes, no, yes |" in private
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
        runs(UNDREAMED_WIKI, [Y] * 6),
        runs(DREAMED_WIKI, [Y] * 3),
        paths={option: inside},
    )

    assert code == 1
    assert option in capsys.readouterr().err
    assert not inside.exists() and public == ""


REFUSED = {
    "fewer than three runs": (
        runs(UNDREAMED_WIKI, [Y] * 6),
        runs(DREAMED_WIKI, [Y] * 2),
        "--second holds 2 runs; a comparison needs 3 runs of each wiki",
    ),
    "runs without a wiki": (
        runs(None, [Y] * 6, configuration="files-alone"),
        runs(DREAMED_WIKI, [Y] * 3),
        "--first holds runs of files-alone, not files-plus-wiki",
    ),
    "the same wiki twice": (
        runs(DREAMED_WIKI, [Y] * 6),
        runs(DREAMED_WIKI, [Y] * 3),
        "both sets of runs were given the same eval wiki",
    ),
    "a set given two wikis": (
        [*runs(UNDREAMED_WIKI, [Y] * 5), *runs(DREAMED_WIKI, [Y])],
        runs(DREAMED_WIKI, [Y] * 3),
        "--first holds runs given more than one eval wiki",
    ),
    "runs over a corpus": (
        runs(UNDREAMED_WIKI, [Y] * 6, cut=None),
        runs(DREAMED_WIKI, [Y] * 3),
        "--first holds a run that wasn't asked over a cut",
    ),
    "two cuts": (
        runs(UNDREAMED_WIKI, [Y] * 6),
        runs(DREAMED_WIKI, [Y] * 3, cut=CUT.model_copy(update={"name": "20261119T000000Z"})),
        "the runs weren't all asked over the same cut",
    ),
    "too few runs for a noise floor": (
        runs(UNDREAMED_WIKI, [Y] * 5),
        runs(DREAMED_WIKI, [Y] * 3),
        "the first wiki was asked 5 runs and the second 3; a noise floor needs the first asked "
        "at least twice as many as the second, 6 runs",
    ),
    "runs that asked different questions": (
        runs(UNDREAMED_WIKI, {"r01": [Y] * 6, "r02": [Y] * 6}),
        runs(DREAMED_WIKI, {"r01": [Y] * 3, "r03": [Y] * 3}),
        "the runs didn't all ask the same questions",
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
