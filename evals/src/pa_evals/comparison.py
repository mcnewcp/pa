"""The comparison report: configurations side by side over repeated runs.

Each configuration's correctness per category is the mean over its runs, with the spread from
the lowest run to the highest. A difference from the first configuration counts only when it is
larger than the spread of either side, so a single run (no spread to go by) never counts. Cost,
turns, and duration are medians over every answer in every run that reports them. The share of
cited episodes opened pools the citations of every answer in every run whose tool calls were
recorded.

Two sets of runs that were each given one eval wiki, and not the same one, also get a wiki
comparison (`compare_wikis`): the second wiki against the first, such as a dreamed copy against
the wiki it was copied from. The second passes when its overall correctness is at least the
first's and no category drops by more than the run-to-run spread, which is the first wiki's
own in that category (`CategoryDrop.spread`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
    single = run_dir / "results.json"
    if single.is_file():
        return [_load(single)]
    numbered = sorted(
        (path for path in run_dir.glob("run-*/results.json") if path.parent.name[4:].isdigit()),
        key=lambda path: int(path.parent.name[4:]),
    )
    if not numbered:
        raise ComparisonError(f"no eval results in {run_dir}")
    return [_load(path) for path in numbered]


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

    if len(labels) == 2:
        verdict = compare_wikis(configurations[labels[0]], configurations[labels[1]])
        if verdict is not None:
            lines += ["", *_wiki_comparison(labels[0], labels[1], verdict)]

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


@dataclass(frozen=True)
class CategoryDrop:
    """One category's correctness (or OVERALL's) on two wikis."""

    category: str
    first: Spread
    second: Spread

    @property
    def drop(self) -> float:
        """How far the second wiki's mean is below the first's; 0 when it isn't."""
        return max(0.0, self.first.mean - self.second.mean)

    @property
    def spread(self) -> float:
        """The run-to-run spread a drop is measured against: the first wiki's own in this
        category, from its lowest run to its highest.

        The second wiki's spread excuses nothing: a wiki that made a category unreliable
        would widen its own allowance. A single run has no spread, so any drop is too far.
        """
        return self.first.width

    @property
    def within_spread(self) -> bool:
        return self.drop <= self.spread + EPSILON


@dataclass(frozen=True)
class WikiVerdict:
    """Whether a second eval wiki does as well as a first, over runs given each."""

    overall: CategoryDrop
    categories: tuple[CategoryDrop, ...]
    """Every category both sets of runs asked."""

    @property
    def overall_holds(self) -> bool:
        """Whether overall correctness on the second wiki is at least the first's."""
        return self.overall.second.mean >= self.overall.first.mean - EPSILON

    @property
    def dropped(self) -> tuple[CategoryDrop, ...]:
        """The categories that dropped by more than the spread."""
        return tuple(category for category in self.categories if not category.within_spread)

    @property
    def passed(self) -> bool:
        return self.overall_holds and not self.dropped


def compare_wikis(first: Sequence[RunResults], second: Sequence[RunResults]) -> WikiVerdict | None:
    """The second runs' eval wiki against the first's, or None unless each set of runs was
    given one eval wiki throughout, and not the same one: a wiki is named by the librarian
    version that built it and the one that dreamed it."""
    if not all(run.wiki is not None for run in (*first, *second)):
        return None
    given = [
        {(run.wiki.librarian_version, run.wiki.dreaming_version) for run in runs if run.wiki}
        for runs in (first, second)
    ]
    if any(len(wikis) != 1 for wikis in given) or given[0] == given[1]:
        return None
    drops: list[CategoryDrop] = []
    for category in [*_categories([first, second]), OVERALL]:
        was, now = correctness(first, category), correctness(second, category)
        if was is not None and now is not None:
            drops.append(CategoryDrop(category, was, now))
    return WikiVerdict(overall=drops[-1], categories=tuple(drops[:-1]))


def _wiki_comparison(first: str, second: str, verdict: WikiVerdict) -> list[str]:
    """The wiki comparison's section of the report: `second`'s wiki against `first`'s."""
    first, second = table_cell(first), table_cell(second)
    lines = [
        "## Wiki against wiki",
        "",
        f"Both sets of runs were given an eval wiki, and not the same one: this is {second}'s "
        f"against {first}'s. It passes when its overall correctness is at least {first}'s, and "
        "no category drops by more than the run-to-run spread. The spread is the one "
        f"{first}'s own runs show in that category, from the lowest to the highest: {second}'s "
        "spread excuses nothing, and a single run has none, so any drop from it is too far.",
        "",
        f"| Category | {first} | {second} | Drop | Spread | Within it |",
        "|---|---|---|---|---|---|",
    ]
    for category in verdict.categories:
        lines.append(
            f"| {table_cell(category.category)} | {format_spread(category.first)} | "
            f"{format_spread(category.second)} | {_drop(category)} | {category.spread:.2f} | "
            f"{'yes' if category.within_spread else 'no'} |"
        )
    overall = verdict.overall
    lines += [
        f"| {OVERALL} | {format_spread(overall.first)} | {format_spread(overall.second)} | "
        f"{_drop(overall)} | n/a | {'yes' if verdict.overall_holds else 'no'} |",
        "",
    ]
    if verdict.passed:
        lines.append(
            f"**Passes**: overall correctness is {overall.second.mean:.2f}, at least "
            f"{overall.first.mean:.2f}, and no category drops by more than the spread."
        )
    if not verdict.overall_holds:
        lines.append(
            f"**Fails**: overall correctness is {overall.second.mean:.2f}, below "
            f"{overall.first.mean:.2f}."
        )
    for category in verdict.dropped:
        lines.append(
            f"**Fails**: {category.category} drops by {category.drop:.2f}, more than the "
            f"spread of {category.spread:.2f}."
        )
    return lines


def _drop(category: CategoryDrop) -> str:
    return f"{category.drop:.2f}" if category.drop > EPSILON else "none"


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
