"""The wiki comparison on real data (#140): files plus wiki over one cut with two eval wikis,
such as the cut's own wiki and a dreamed copy of it.

Like the real-data verdict (`pa_evals.verdict`), it comes in two parts. The public report,
which can be committed, holds numbers only: the second wiki's drop from the first and the noise
floor it is judged against, overall, by tier and by category, how many questions fell in each
tier, whether it passes (`pa_evals.comparison.compare_wikis`), evidence recall, citation
validity, and cost. Nothing from the real-data eval set reaches it, not a question's id, and
it names the live wiki, never its commit. The private analysis, kept under the data root,
names the fallen questions first, then holds the questions whose score fell, every question's
outcome in every run, and each failed answer, with what its session never opened.

A comparison in which any answer failed to run, on either wiki, isn't judged: both reports are
still written, and each says so in place of a pass or a fail, with how many answers failed on
each wiki, for those runs to be run again.

Each wiki is asked over at least `MIN_RUNS` runs, and the first over at least twice as many as
the second: the noise floor is taken between halves of the first wiki's own runs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pa_evals.comparison import (
    ComparisonError,
    WikiComparison,
    answers,
    compare_wikis,
    failed_to_run,
    format_spread,
    same_questions,
    wiki_comparison_section,
)
from pa_evals.report import format_cost, format_score, format_turns
from pa_evals.results import RunResults, cited_opened, median
from pa_evals.verdict import (
    FILES_PLUS_WIKI,
    MIN_RUNS,
    SETUP_SHOWN,
    failure_analysis,
    format_dollars,
    overall_spread,
    total_cost,
)


@dataclass(frozen=True)
class WikiRuns:
    """The runs given each of the two eval wikis a comparison is of, the second against the
    first, with what the reports call each."""

    first: Sequence[RunResults]
    second: Sequence[RunResults]
    comparison: WikiComparison

    @property
    def labels(self) -> tuple[str, str]:
        """What the reports call each wiki: by whether it was dreamed, when that tells them
        apart."""
        first, second = (_dreamed(runs) for runs in (self.first, self.second))
        if first == second:
            return "the first wiki", "the second wiki"
        return _DREAMED[first], _DREAMED[second]

    def by_label(self) -> dict[str, Sequence[RunResults]]:
        first, second = self.labels
        return {first: self.first, second: self.second}


_DREAMED = {True: "the dreamed wiki", False: "the undreamed wiki"}


def _dreamed(runs: Sequence[RunResults]) -> bool:
    """Whether the eval wiki these runs were given was dreamed."""
    wiki = runs[0].wiki
    return wiki is not None and wiki.dreaming_version is not None


def check_wiki_runs(name: str, runs: Sequence[RunResults]) -> None:
    """Refuse runs (given by the option `name`) that aren't all files plus wiki over a cut
    with one eval wiki, or that are fewer than MIN_RUNS."""
    others = sorted({run.configuration or "unnamed" for run in runs} - {FILES_PLUS_WIKI})
    if others:
        raise ComparisonError(f"{name} holds runs of {', '.join(others)}, not {FILES_PLUS_WIKI}")
    if any(run.cut is None for run in runs):
        raise ComparisonError(f"{name} holds a run that wasn't asked over a cut")
    given = {(run.wiki.librarian_version, run.wiki.dreaming_version) for run in runs if run.wiki}
    if len(given) != 1 or any(run.wiki is None for run in runs):
        raise ComparisonError(f"{name} holds runs given more than one eval wiki, or none")
    if len(runs) < MIN_RUNS:
        raise ComparisonError(
            f"{name} holds {len(runs)} run{'s' if len(runs) != 1 else ''}; a comparison needs "
            f"{MIN_RUNS} runs of each wiki"
        )


def wiki_runs(first: Sequence[RunResults], second: Sequence[RunResults]) -> WikiRuns:
    """The two sets of runs as a comparison of `second`'s eval wiki against `first`'s, each
    set already checked (`check_wiki_runs`). Refused unless they were asked the same
    questions over the same cut, and given different wikis, the first over at least twice as
    many runs as the second."""
    cuts = sorted({run.cut.name for run in (*first, *second) if run.cut is not None})
    if len(cuts) != 1:
        raise ComparisonError(f"the runs weren't all asked over the same cut ({', '.join(cuts)})")
    if not same_questions([*first, *second]):
        raise ComparisonError(
            "the runs didn't all ask the same questions: a comparison is question by question"
        )
    comparison = compare_wikis(first, second)
    if comparison is None:
        raise ComparisonError(
            "both sets of runs were given the same eval wiki: a comparison is of two wikis"
        )
    if comparison.overall.floor is None:
        raise ComparisonError(
            f"the first wiki was asked {len(first)} runs and the second {len(second)}; a noise "
            "floor needs the first asked at least twice as many as the second, "
            f"{2 * len(second)} runs"
        )
    return WikiRuns(first, second, comparison)


def render_public(runs: WikiRuns) -> str:
    """The public report: numbers only."""
    one = runs.first[0]
    assert one.cut is not None
    cut = one.cut
    first, second = runs.labels
    settings = [
        f"  - {key.capitalize()}: {one.setup[key]}" for key in SETUP_SHOWN if key in one.setup
    ]
    wikis = [
        f"  - {label.capitalize()}: {given[0].wiki.versions}"
        for label, given in runs.by_label().items()
        if given[0].wiki is not None
    ]
    lines = [
        "# Wiki against wiki on real data",
        "",
        "Numbers only. The questions, answers and failure analysis are in the private analysis "
        "under the data root.",
        "",
        f"- **The cut:** taken {cut.as_of.strftime('%Y-%m-%d %H:%M UTC')}, {cut.episodes} "
        f"episodes{' and the live wiki' if cut.wiki_commit else ', no wiki'}.",
        f"- **The set:** {len(one.questions)} questions, asked at the cut's time.",
        f"- **The runs:** files plus wiki, {first} {len(runs.first)}, {second} {len(runs.second)}.",
        "- **The wikis:**",
        *wikis,
        *(["- **The setup:**", *settings] if settings else []),
        "",
        *wiki_comparison_section(first, second, runs.comparison),
        "",
        "## Evidence, citations and cost",
        "",
        f"| | {first.capitalize()} | {second.capitalize()} |",
        "|---|---|---|",
    ]
    spreads = (("Evidence recall", "evidence_recall"), ("Citation validity", "citation_validity"))
    for label, score in spreads:
        cells = [format_spread(overall_spread(given, score)) for given in runs.by_label().values()]
        lines.append(f"| {label} | {' | '.join(cells)} |")
    rows = {
        "Cited episodes opened": lambda given: format_score(cited_opened(answers(given))),
        "Total cost": lambda given: format_dollars(total_cost(answers(given))),
        "Per answer, median cost": lambda given: format_cost(
            median([q.cost_usd for q in answers(given)])
        ),
        "Per answer, median turns": lambda given: format_turns(
            median([q.turns for q in answers(given)])
        ),
        "Failed answers (agent or judge)": lambda given: str(
            sum(q.status == "failed" for q in answers(given))
        ),
    }
    for label, cell in rows.items():
        cells = [cell(given) for given in runs.by_label().values()]
        lines.append(f"| {label} | {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


def render_private(runs: WikiRuns) -> str:
    """The private analysis: the fallen questions, the questions whose score fell, every
    question's outcome in every run on each wiki, and each failed answer. One that isn't
    judged says so first."""
    first, second = (label.capitalize() for label in runs.labels)
    fallen = [
        "## The fallen questions",
        "",
        f"Right in every run on {runs.labels[0]} and wrong in every run on {runs.labels[1]}. "
        "Any fails the comparison.",
        "",
    ]
    if any(runs.comparison.failed):
        fallen += [
            f"**Not judged**: {failed_to_run(*runs.labels, runs.comparison)}. An answer that "
            "failed to run counts here as not right, so this list and the scores below wait "
            "for those runs to be run again.",
            "",
        ]
    if runs.comparison.fallen:
        fallen += ["| Question | Category | Tier |", "|---|---|---|"]
        fallen += [
            f"| {q.id} | {q.category} | {'-' if q.tier is None else q.tier} |"
            for q in runs.comparison.fallen
        ]
    else:
        fallen.append("None.")
    fell = [
        "",
        "## The questions whose score fell",
        "",
        "A question's score on a wiki is the share of that wiki's runs that got it right. "
        f"These are lower on {runs.labels[1]}, the furthest fall first.",
        "",
    ]
    if runs.comparison.fell:
        fell += [
            f"| Question | Category | Tier | {first} | {second} | Fall |",
            "|---|---|---|---|---|---|",
        ]
        fell += [
            f"| {q.id} | {q.category} | {'-' if q.tier is None else q.tier} | {q.first:.2f} | "
            f"{q.second:.2f} | {q.fall:.2f} |"
            for q in runs.comparison.fell
        ]
    else:
        fell.append("None fell.")
    return failure_analysis(
        "# Wiki against wiki on real data, private: the failure analysis",
        runs.by_label(),
        before=[*fallen, *fell],
    )
