"""The comparison report: configurations side by side over repeated runs.

Each configuration's correctness per category is the mean over its runs, with the spread from
the lowest run to the highest. A difference from the first configuration counts only when it is
larger than the spread of either side, so a single run (no spread to go by) never counts. Cost,
turns, and duration are medians over every answer in every run that reports them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pa_evals.report import (
    format_cost,
    format_duration,
    format_turns,
    new_run_dir,
    write_results,
)
from pa_evals.results import QuestionResult, RunResults, median

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
        named = f" (configuration {', '.join(names)})" if names else ""
        lines.append(f"- {_cell(label)}: {len(runs)} run{'s' if len(runs) != 1 else ''}{named}")
    lines += [
        "",
        "## Correctness by category",
        "",
        "Mean over runs, with the spread from the lowest run to the highest.",
    ]
    if others:
        lines.append(
            f"A difference from {_cell(reference)} counts only when it is larger than the spread "
            "of either side, and is marked (counts); a single run has no spread, so it never "
            "counts."
        )
    header = [_cell(label) for label in labels] + [
        f"{_cell(other)} vs {_cell(reference)}" for other in others
    ]
    lines += [
        "",
        f"| Category | {' | '.join(header)} |",
        f"|---|{'---|' * len(header)}",
    ]
    for category in [*categories, OVERALL]:
        spreads = {label: correctness(runs, category) for label, runs in configurations.items()}
        cells = [_spread(spreads[label]) for label in labels]
        cells += [_difference(spreads[reference], spreads[other]) for other in others]
        lines.append(f"| {_cell(category)} | {' | '.join(cells)} |")

    lines += [
        "",
        "## Efficiency by category",
        "",
        "Median cost, turns, and duration over every answer in every run that reports them "
        "(n/a: none do).",
        "",
        f"| Category | {' | '.join(_cell(label) for label in labels)} |",
        f"|---|{'---|' * len(labels)}",
    ]
    for category in [*categories, OVERALL]:
        cells = [
            _efficiency(_answers(runs, None if category == OVERALL else category))
            for runs in configurations.values()
        ]
        lines.append(f"| {_cell(category)} | {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


def _categories(configurations: Iterable[Sequence[RunResults]]) -> list[str]:
    """Every category asked, in the order categories first appear."""
    seen: dict[str, None] = {}
    for runs in configurations:
        for run in runs:
            seen.update(dict.fromkeys(run.categories))
    return list(seen)


def _answers(runs: Sequence[RunResults], category: str | None) -> list[QuestionResult]:
    return [q for run in runs for q in run.questions if category is None or q.category == category]


def _spread(spread: Spread | None) -> str:
    if spread is None:
        return "n/a"
    return f"{spread.mean:.2f} ({spread.low:.2f} to {spread.high:.2f})"


def _difference(reference: Spread | None, other: Spread | None) -> str:
    if reference is None or other is None:
        return "n/a"
    mark = " (counts)" if counts(reference, other) else ""
    return f"{other.mean - reference.mean:+.2f}{mark}"


def _efficiency(answers: Sequence[QuestionResult]) -> str:
    cost = median([q.cost_usd for q in answers])
    turns = median([q.turns for q in answers])
    duration = median([q.duration_ms for q in answers])
    if (cost, turns, duration) == (None, None, None):
        return "n/a"
    return f"{format_cost(cost)}, {format_turns(turns)} turns, {format_duration(duration)}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
