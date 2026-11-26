"""The comparison report: configurations side by side over repeated runs.

Each configuration's correctness per category is the mean over its runs, with the spread from
the lowest run to the highest. A difference from the first configuration counts only when it is
larger than the spread of either side, so a single run (no spread to go by) never counts. Cost,
turns, and duration are medians over every answer in every run that reports them. The share of
cited episodes opened pools the citations of every answer in every run whose tool calls were
recorded.

Two sets of runs that were each given one eval wiki, and not the same one, and asked the same
questions, also get a wiki comparison (`compare_wikis`): the second wiki against the first,
such as a dreamed copy against the wiki it was copied from. It is judged against a noise floor
(#156), with a one-answer allowance and a check for fallen questions (#171):

- **A question's score** on a wiki is the share of that wiki's runs that got it right. An
  answer that failed (the agent or the judge raised) isn't right.
- **The drop** is the mean over questions of the first wiki's score minus the second's:
  overall, for each tier, and for each category.
- **The noise floor** is how far the first wiki falls from itself: the largest drop between
  any two halves of its own runs. A half is as many runs as the second wiki was asked, so the
  first is asked twice as many. Every pair of halves that share no run is tried, each way
  round; where the first wiki was asked more than twice the second's runs, each pair leaves
  the rest out. Where it was asked fewer than twice, there is no floor, and the comparison
  isn't judged.
- **The one-answer allowance** is what one of the second wiki's answers moves a group's drop
  by: 1 over the group's questions times the second wiki's runs. A tier the first wiki gets
  entirely right has a floor of zero, and the allowance keeps one wrong answer from failing it.
- **A fallen question** is one that every run on the first wiki got right and every run on the
  second got wrong. Its tier's mean can hide it, where other questions in the tier rose.
- **The second wiki passes** when no question fell, and the drop is no larger than the noise
  floor or the one-answer allowance, overall and in every tier. An eval set without tiers is
  judged overall and in every category instead. A report marks a group that is within only by
  the allowance.
- **A tier or category of fewer than `FEW_QUESTIONS` questions** counts like any other, and
  the report marks it: one answer moves its drop a long way.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from math import comb
from pathlib import Path

from pydantic import ValidationError

from pa_core.errors import PaError
from pa_evals.report import (
    format_cost,
    format_duration,
    format_score,
    format_turns,
    new_run_dir,
    write_results,
)
from pa_evals.results import QuestionResult, RunResults, cited_opened, median

OVERALL = "Overall"
COMPARISON = "comparison.md"


def write_runs(
    runs: Sequence[RunResults],
    runs_dir: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Write repeated runs of one configuration into a new run directory under `runs_dir`.

    Each run goes in `run-<n>/` as a single run's directory would hold it, beside
    `comparison.md`. A single run is written as `write_run` writes it. Returns the run
    directory.
    """
    run_dir = new_run_dir(runs_dir, now)
    if len(runs) == 1:
        write_results(runs[0], run_dir)
        return run_dir
    for number, results in enumerate(runs, start=1):
        (run_dir / f"run-{number}").mkdir()
        write_results(results, run_dir / f"run-{number}")
    (run_dir / COMPARISON).write_text(render_comparison(by_configuration(runs)))
    return run_dir


def load_runs(run_dir: Path) -> list[RunResults]:
    """The runs in a run directory: its own `results.json`, or each `run-<n>/` in order.

    Results written before efficiency figures or configuration names were recorded load with
    them as None.
    """
    return [_load(path) for path in run_files(run_dir)]


def run_files(run_dir: Path) -> list[Path]:
    """Each run's `results.json` in a run directory: its own, or each `run-<n>/`'s in order."""
    single = run_dir / "results.json"
    if single.is_file():
        return [single]
    numbered = sorted(
        (path for path in run_dir.glob("run-*/results.json") if path.parent.name[4:].isdigit()),
        key=lambda path: int(path.parent.name[4:]),
    )
    if not numbered:
        raise ComparisonError(f"no eval results in {run_dir}")
    return numbered


def _load(path: Path) -> RunResults:
    try:
        return RunResults.model_validate_json(path.read_text())
    except ValidationError as error:
        raise ComparisonError(f"{path} is not eval results: {error}") from error


class ComparisonError(PaError):
    """Run directories that can't be compared."""


def by_configuration(runs: Sequence[RunResults]) -> dict[str, list[RunResults]]:
    """Runs keyed by configuration name, in the order configurations first appear."""
    grouped: dict[str, list[RunResults]] = {}
    for run in runs:
        grouped.setdefault(run.configuration or "unnamed", []).append(run)
    return grouped


@dataclass(frozen=True)
class Spread:
    """A score's mean over runs, and its lowest and highest run."""

    mean: float
    low: float
    high: float
    runs: int

    @classmethod
    def over(cls, scores: Sequence[float]) -> Spread:
        return cls(sum(scores) / len(scores), min(scores), max(scores), len(scores))

    @property
    def width(self) -> float:
        return self.high - self.low


def counts(reference: Spread, other: Spread) -> bool:
    """Whether `other` differs from `reference` by more than the spread of either.

    With a single run on either side there is no spread to go by, so nothing counts.
    """
    if reference.runs < 2 or other.runs < 2:
        return False
    return abs(other.mean - reference.mean) > max(reference.width, other.width)


def correctness(runs: Sequence[RunResults], category: str) -> Spread | None:
    """Correctness on `category` (or OVERALL) over the runs that asked it, or None if none did."""
    if category == OVERALL:
        return Spread.over([run.overall.correctness for run in runs])
    scores = [run.categories[category].correctness for run in runs if category in run.categories]
    return Spread.over(scores) if scores else None


def tier_correctness(runs: Sequence[RunResults], tier: int) -> Spread | None:
    """Correctness on `tier` over the runs that asked it, or None if none did."""
    scores = [run.tiers[tier].correctness for run in runs if tier in run.tiers]
    return Spread.over(scores) if scores else None


def render_comparison(configurations: Mapping[str, Sequence[RunResults]]) -> str:
    """The comparison report for each configuration's runs, the first being the reference."""
    if not configurations or not all(configurations.values()):
        raise ValueError("a comparison needs at least one configuration, each with a run")
    labels = list(configurations)
    reference, others = labels[0], labels[1:]
    categories = _categories(configurations.values())

    lines = ["# Eval comparison", ""]
    for label, runs in configurations.items():
        names = sorted({run.configuration for run in runs if run.configuration} - {label})
        wikis = sorted({run.wiki.versions for run in runs if run.wiki})
        notes = [f"configuration {', '.join(names)}"] if names else []
        notes += [f"wiki {' and '.join(wikis)}"] if wikis else []
        noted = f" ({'; '.join(notes)})" if notes else ""
        plural = "s" if len(runs) != 1 else ""
        lines.append(f"- {table_cell(label)}: {len(runs)} run{plural}{noted}")
    lines += [
        "",
        "## Correctness by category",
        "",
        "Mean over runs, with the spread from the lowest run to the highest.",
    ]
    if others:
        lines.append(
            f"A difference from {table_cell(reference)} counts only when it is larger than the "
            "spread of either side, and is marked (counts); a single run has no spread, so it "
            "never counts."
        )
    header = [table_cell(label) for label in labels] + [
        f"{table_cell(other)} vs {table_cell(reference)}" for other in others
    ]
    lines += [
        "",
        f"| Category | {' | '.join(header)} |",
        f"|---|{'---|' * len(header)}",
    ]
    for category in [*categories, OVERALL]:
        spreads = {label: correctness(runs, category) for label, runs in configurations.items()}
        cells = [format_spread(spreads[label]) for label in labels]
        cells += [format_difference(spreads[reference], spreads[other]) for other in others]
        lines.append(f"| {table_cell(category)} | {' | '.join(cells)} |")

    tiers = sorted({tier for runs in configurations.values() for run in runs for tier in run.tiers})
    if tiers:
        lines += [
            "",
            "## Correctness by tier",
            "",
            "The same questions by their tier in the eval set, read as the categories are.",
            "",
            f"| Tier | {' | '.join(header)} |",
            f"|---|{'---|' * len(header)}",
        ]
    for tier in tiers:
        spreads = {label: tier_correctness(runs, tier) for label, runs in configurations.items()}
        cells = [format_spread(spreads[label]) for label in labels]
        cells += [format_difference(spreads[reference], spreads[other]) for other in others]
        lines.append(f"| {tier} | {' | '.join(cells)} |")

    if len(labels) == 2:
        comparison = compare_wikis(configurations[labels[0]], configurations[labels[1]])
        if comparison is not None:
            lines += ["", *wiki_comparison_section(labels[0], labels[1], comparison)]

    lines += [
        "",
        "## Efficiency by category",
        "",
        "Median cost, turns, and duration over every answer in every run that reports them "
        "(n/a: none do).",
        "",
        f"| Category | {' | '.join(table_cell(label) for label in labels)} |",
        f"|---|{'---|' * len(labels)}",
    ]
    for category in [*categories, OVERALL]:
        cells = [
            _efficiency(answers(runs, None if category == OVERALL else category))
            for runs in configurations.values()
        ]
        lines.append(f"| {table_cell(category)} | {' | '.join(cells)} |")

    lines += [
        "",
        "## Cited episodes opened",
        "",
        "Of the episodes cited by every answer in every run whose tool calls were recorded, the "
        "share whose contents reached the session (n/a: none cite anything).",
        "",
        f"| Category | {' | '.join(table_cell(label) for label in labels)} |",
        f"|---|{'---|' * len(labels)}",
    ]
    for category in [*categories, OVERALL]:
        cells = [
            format_score(cited_opened(answers(runs, None if category == OVERALL else category)))
            for runs in configurations.values()
        ]
        lines.append(f"| {table_cell(category)} | {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


EPSILON = 1e-9
"""What two means of the same scores can differ by, from the order they were added in."""
FEW_QUESTIONS = 5
"""A tier or category with fewer questions than this is marked (few) in a wiki comparison: it
counts like any other, but a single answer moves its drop a long way."""


@dataclass(frozen=True)
class QuestionScore:
    """One question's score on each of two wikis: the share of that wiki's runs that got it
    right."""

    id: str
    category: str
    tier: int | None
    first: float
    second: float

    @property
    def fall(self) -> float:
        return self.first - self.second

    @property
    def fallen(self) -> bool:
        """Whether it is a fallen question: right in every run on the first wiki and in none
        on the second. An answer that failed isn't right, as in its score."""
        return self.first == 1 and self.second == 0


@dataclass(frozen=True)
class Drop:
    """A group of questions on two wikis: all of them (OVERALL), one tier's, or one
    category's."""

    group: str
    questions: int
    first: float
    """The mean over its questions of the first wiki's score."""
    second: float
    floor: float | None
    """The noise floor the drop is judged against: the largest drop between any two halves of
    the first wiki's own runs. None where the first wiki was asked too few runs to have one."""
    allowance: float
    """The one-answer allowance: what one of the second wiki's answers in the group moves the
    drop by, 1 over its questions times the second wiki's runs."""

    @property
    def drop(self) -> float:
        """The mean over its questions of the first wiki's score minus the second's: negative
        when the second wiki did better."""
        return self.first - self.second

    @property
    def within_floor(self) -> bool | None:
        """Whether the drop is no larger than the noise floor, or None without a floor."""
        return None if self.floor is None else self.drop <= self.floor + EPSILON

    @property
    def within(self) -> bool | None:
        """Whether the drop is no larger than the noise floor or the one-answer allowance, so
        that one answer never fails a group. None without a floor."""
        if self.within_floor is None:
            return None
        return self.within_floor or self.drop <= self.allowance + EPSILON

    @property
    def only_by_allowance(self) -> bool:
        """Whether the drop is within by the one-answer allowance and beyond the noise
        floor."""
        return self.within is True and self.within_floor is False

    @property
    def few(self) -> bool:
        return self.questions < FEW_QUESTIONS


@dataclass(frozen=True)
class WikiComparison:
    """Whether a second eval wiki does as well as a first, over runs given each."""

    overall: Drop
    tiers: tuple[Drop, ...]
    """Each tier, lowest first; empty for an eval set without tiers."""
    categories: tuple[Drop, ...]
    questions: tuple[QuestionScore, ...]
    """Every question's score on each wiki, in eval set order."""
    runs: tuple[int, int]
    """How many runs the first wiki was asked, and the second."""

    @property
    def deciding(self) -> tuple[Drop, ...]:
        """The groups that decide beside OVERALL: the tiers of a tiered eval set, and the
        categories of one without tiers."""
        return self.tiers or self.categories

    @property
    def pairs(self) -> int:
        """How many pairs of halves the noise floor was taken over, a pair counted once for
        both ways round."""
        asked, half = self.runs
        return comb(asked, half) * comb(asked - half, half) // 2

    @property
    def judged(self) -> tuple[Drop, ...]:
        """The groups whose drop decides: OVERALL, then the deciding groups."""
        return (self.overall, *self.deciding)

    @property
    def beyond(self) -> tuple[Drop, ...]:
        """The groups judged whose drop is larger than both the noise floor and the one-answer
        allowance."""
        return tuple(group for group in self.judged if group.within is False)

    @property
    def only_by_allowance(self) -> tuple[Drop, ...]:
        """The groups judged whose drop is within by the one-answer allowance alone."""
        return tuple(group for group in self.judged if group.only_by_allowance)

    @property
    def fallen(self) -> tuple[QuestionScore, ...]:
        """The fallen questions, in eval set order: each right in every run on the first wiki
        and wrong in every run on the second."""
        return tuple(question for question in self.questions if question.fallen)

    @property
    def passed(self) -> bool | None:
        """Whether no question fell and the drop is no larger than the noise floor or the
        one-answer allowance, overall and in every tier (or category, without tiers). None
        where there is no floor to judge by."""
        return None if self.overall.floor is None else not (self.fallen or self.beyond)

    @property
    def fell(self) -> tuple[QuestionScore, ...]:
        """The questions whose score is lower on the second wiki, the furthest fall first."""
        fallen = [question for question in self.questions if question.fall > EPSILON]
        return tuple(sorted(fallen, key=lambda question: -question.fall))


def compare_wikis(
    first: Sequence[RunResults], second: Sequence[RunResults]
) -> WikiComparison | None:
    """The second runs' eval wiki against the first's, by the noise floor, the one-answer
    allowance and the fallen questions (see the module's docstring).

    None unless each set of runs was given one eval wiki throughout, and not the same one (a
    wiki is named by the librarian version that built it and the one that dreamed it), and
    every run asked the same questions. Where the first wiki was asked fewer than twice the
    second's runs there is no noise floor: every `Drop.floor` is None, and so is `passed`.
    """
    if not all(run.wiki is not None for run in (*first, *second)):
        return None
    given = [
        {(run.wiki.librarian_version, run.wiki.dreaming_version) for run in runs if run.wiki}
        for runs in (first, second)
    ]
    if any(len(wikis) != 1 for wikis in given) or given[0] == given[1]:
        return None
    asked = first[0].questions
    if not asked or not same_questions([*first, *second]):
        return None

    right = [[_right(run, asked) for run in runs] for runs in (first, second)]
    groups: dict[str, list[int]] = {OVERALL: list(range(len(asked)))}
    for tier in sorted({q.tier for q in asked if q.tier is not None}):
        groups[f"Tier {tier}"] = [n for n, q in enumerate(asked) if q.tier == tier]
    tiers = len(groups) - 1
    for category in dict.fromkeys(q.category for q in asked):
        groups[category] = [n for n, q in enumerate(asked) if q.category == category]
    half = len(second)
    floors = _noise_floors(right[0], groups, half) if len(first) >= 2 * half else None

    drops = [
        Drop(
            group,
            len(members),
            first=_score(right[0], members),
            second=_score(right[1], members),
            floor=None if floors is None else floors[group],
            allowance=1 / (len(members) * half),
        )
        for group, members in groups.items()
    ]
    return WikiComparison(
        overall=drops[0],
        tiers=tuple(drops[1 : 1 + tiers]),
        categories=tuple(drops[1 + tiers :]),
        questions=tuple(
            QuestionScore(q.id, q.category, q.tier, _score(right[0], [n]), _score(right[1], [n]))
            for n, q in enumerate(asked)
        ),
        runs=(len(first), len(second)),
    )


def same_questions(runs: Sequence[RunResults]) -> bool:
    """Whether every run asked the same questions."""
    return len({frozenset(q.id for q in run.questions) for run in runs}) == 1


def _right(run: RunResults, asked: Sequence[QuestionResult]) -> list[bool]:
    """Whether the run got each of the questions `asked` right, in their order."""
    correct = {q.id: q.correct is True for q in run.questions}
    return [correct[q.id] for q in asked]


def _score(right: Sequence[Sequence[bool]], members: Sequence[int]) -> float:
    """The mean over the questions `members` of their score: the share of the runs (`right`,
    each run's questions) that got each right."""
    return sum(run[n] for run in right for n in members) / (len(right) * len(members))


def _noise_floors(
    right: Sequence[Sequence[bool]], groups: Mapping[str, Sequence[int]], half: int
) -> dict[str, float]:
    """Each group's noise floor: the largest drop between any two halves of one wiki's runs
    (`right`, each run's questions). A half is `half` runs, and every pair of halves that
    share no run is tried, each way round; runs beyond the two halves sit that pair out."""
    correct = {
        group: [sum(run[n] for n in members) for run in right] for group, members in groups.items()
    }
    floors = dict.fromkeys(groups, 0.0)
    runs = range(len(right))
    for one in combinations(runs, half):
        for other in combinations([run for run in runs if run not in one], half):
            for group, per_run in correct.items():
                gap = sum(per_run[run] for run in one) - sum(per_run[run] for run in other)
                floors[group] = max(floors[group], gap / (half * len(groups[group])))
    return floors


def wiki_comparison_section(first: str, second: str, comparison: WikiComparison) -> list[str]:
    """The wiki comparison's section of a report, as its lines: `second`'s wiki against
    `first`'s, each named as the report names it. Numbers only."""
    first, second = table_cell(first), table_cell(second)
    asked, half = comparison.runs
    kind = "tier" if comparison.tiers else "category"
    judged = comparison.judged
    floor = f"- **The noise floor** is the largest drop between any two halves of {first}'s own "
    if comparison.passed is None:
        floor += (
            f"runs, a half being as many runs as {second} was asked. There is none here: it "
            f"needs {first} asked at least twice as many runs as {second}, and it was asked "
            f"{asked} against {half}."
        )
    else:
        left = f", each leaving {asked - 2 * half} of them out" if asked > 2 * half else ""
        floor += (
            f"runs. A half is {_count(half, 'run')}, as many as {second} was asked, and every "
            "pair of halves that share no run is tried, each way round: "
            f"{_count(comparison.pairs, 'pair')} from its {asked} runs{left}."
        )
    lines = [
        "## Wiki against wiki",
        "",
        f"Both sets of runs were given an eval wiki, and not the same one: this is {second}'s "
        f"against {first}'s, over the {_count(len(comparison.questions), 'question')} every "
        "run asked.",
        "",
        "- **A question's score** on a wiki is the share of that wiki's runs that got it right.",
        f"- **The drop** is the mean over questions of {first}'s score minus {second}'s, so a "
        "rise is negative.",
        floor,
        f"- **The one-answer allowance** is what one of {second}'s answers moves a group's "
        f"drop by: 1 over the group's questions times {second}'s {_count(half, 'run')}. A drop "
        "no larger than it is within, whatever the noise floor, so one answer never fails a "
        f"{kind}.",
        f"- **A fallen question** is one that every run on {first} got right and every run on "
        f"{second} got wrong.",
        "- **It passes** when no question fell and the drop is no larger than the noise floor "
        f"or the one-answer allowance, overall and in every {kind}.",
    ]
    if any(group.few for group in judged):
        lines.append(
            f"- **(few)** marks fewer than {FEW_QUESTIONS} questions. Such a {kind} counts like "
            f"any other, but one of {second}'s answers moves its drop by "
            f"{1 / ((FEW_QUESTIONS - 1) * half):.3f} or more, so its result says little by "
            "itself."
        )
    lines += [
        "",
        f"| | Questions | {first} | {second} | Drop | Noise floor | Within it |",
        "|---|---|---|---|---|---|---|",
    ]
    for group in judged:
        within = {None: "n/a", True: "yes", False: "no"}[group.within]
        if group.only_by_allowance:
            within = f"only by the allowance ({_thousandths(group.allowance)})"
        few = " (few)" if group.few else ""
        lines.append(
            f"| {table_cell(group.group)} | {group.questions}{few} | {_cells(group)} | {within} |"
        )
    lines.append("")
    allowed = comparison.only_by_allowance
    if comparison.passed and allowed:
        lines.append(
            "**Passes**: no question fell, and the drop is no larger than the noise floor or "
            f"the one-answer allowance, overall and in every {kind}. Only by the allowance: "
            f"{', '.join(_named(group) for group in allowed)}."
        )
    elif comparison.passed:
        overall = comparison.overall
        lines.append(
            "**Passes**: no question fell, and the drop is no larger than the noise floor, "
            f"overall ({_thousandths(overall.drop)} against {_thousandths(overall.floor)}) and "
            f"in every {kind}."
        )
    if comparison.passed is None:
        lines.append("**Not judged**: without a noise floor, a drop can't be told from noise.")
    elif comparison.fallen:
        lines.append(
            f"**Fails**: {_count(len(comparison.fallen), 'question')} fell: "
            f"{_fallen_by_group(comparison)}."
        )
    for group in comparison.beyond:
        lines.append(
            f"**Fails**: {_named(group)} drops by {_thousandths(group.drop)}, more than the "
            f"noise floor of {_thousandths(group.floor)} and the one-answer allowance of "
            f"{_thousandths(group.allowance)}."
        )
    if comparison.tiers:
        lines += [
            "",
            "### By category",
            "",
            "The same drop and noise floor by category. The eval set has tiers, so its "
            "categories decide nothing.",
            "",
            f"| Category | Questions | {first} | {second} | Drop | Noise floor |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {table_cell(group.group)} | {group.questions} | {_cells(group)} |"
            for group in comparison.categories
        ]
    return lines


def _named(group: Drop) -> str:
    """A group as a sentence names it: overall, tier 3, or the category."""
    return f"{group.group[0].lower()}{group.group[1:]}"


def _fallen_by_group(comparison: WikiComparison) -> str:
    """How many questions fell in each tier that has one, lowest first; in each category, for
    an eval set without tiers. Counts only: a report of numbers names no question."""
    fallen = sorted(comparison.fallen, key=lambda question: question.tier or 0)
    groups = Counter(
        question.category if question.tier is None else f"tier {question.tier}"
        for question in fallen
    )
    return ", ".join(f"{number} in {group}" for group, number in groups.items())


def _cells(group: Drop) -> str:
    """A group's score on each wiki, its drop, and its noise floor, as table cells."""
    return (
        f"{group.first:.2f} | {group.second:.2f} | {_thousandths(group.drop)} | "
        f"{_thousandths(group.floor)}"
    )


def _thousandths(value: float | None) -> str:
    """A drop or a noise floor to three decimals, since one answer of 50 questions over 3
    runs moves it by less than 0.01; n/a where there is none."""
    return "n/a" if value is None else f"{round(value, 3) + 0.0:.3f}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _categories(configurations: Iterable[Sequence[RunResults]]) -> list[str]:
    """Every category asked, in the order categories first appear."""
    seen: dict[str, None] = {}
    for runs in configurations:
        for run in runs:
            seen.update(dict.fromkeys(run.categories))
    return list(seen)


def answers(runs: Sequence[RunResults], category: str | None = None) -> list[QuestionResult]:
    """Every answer in every run, on `category` alone if given."""
    return [q for run in runs for q in run.questions if category is None or q.category == category]


def format_spread(spread: Spread | None) -> str:
    """A mean over runs with its spread, or n/a where no run has it."""
    if spread is None:
        return "n/a"
    return f"{spread.mean:.2f} ({spread.low:.2f} to {spread.high:.2f})"


def format_difference(reference: Spread | None, other: Spread | None) -> str:
    """`other`'s mean less `reference`'s, marked (counts) when larger than either's spread."""
    if reference is None or other is None:
        return "n/a"
    mark = " (counts)" if counts(reference, other) else ""
    return f"{other.mean - reference.mean:+.2f}{mark}"


def _efficiency(results: Sequence[QuestionResult]) -> str:
    cost = median([q.cost_usd for q in results])
    turns = median([q.turns for q in results])
    duration = median([q.duration_ms for q in results])
    if (cost, turns, duration) == (None, None, None):
        return "n/a"
    return f"{format_cost(cost)}, {format_turns(turns)} turns, {format_duration(duration)}"


def table_cell(text: str) -> str:
    """`text` made safe for one Markdown table cell: its pipes escaped, on one line."""
    return text.replace("|", "\\|").replace("\n", " ")
