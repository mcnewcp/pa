"""Writing an eval run to disk: `report.md` and `results.json` in a timestamped run directory.

Repeated runs are written by `pa_evals.comparison.write_runs`, one directory per run beside a
comparison of them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pa_evals.results import InjectionChecks, LibrarianRun, QuestionResult, RunResults, Scores

_SCORE_COLUMNS = "Questions | Failed | Correctness | Evidence recall | Citation validity"


def write_run(
    results: RunResults,
    runs_dir: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Write `results` into a new run directory under `runs_dir`, named by the UTC time.

    The files hold no wall-clock time, so the same results always write the same bytes.
    Returns the run directory.
    """
    run_dir = new_run_dir(runs_dir, now)
    write_results(results, run_dir)
    return run_dir


def write_results(results: RunResults, run_dir: Path) -> None:
    """Write `results.json` and `report.md` for `results` into `run_dir`, which exists."""
    (run_dir / "results.json").write_text(results.model_dump_json(indent=2) + "\n")
    (run_dir / "report.md").write_text(render_report(results))


def new_run_dir(runs_dir: Path, now: Callable[[], datetime]) -> Path:
    """A new directory under `runs_dir`, named by the UTC time (suffixed if that's taken)."""
    return _new_dir(runs_dir, now().astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ"))


def _new_dir(runs_dir: Path, name: str) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    candidate, attempt = runs_dir / name, 1
    while True:
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            attempt += 1
            candidate = runs_dir / f"{name}-{attempt}"


def render_report(results: RunResults) -> str:
    overall = results.overall
    ingest = results.ingest
    lines = ["# Eval run", ""]
    if results.configuration is not None:
        lines.append(f"- Configuration: {results.configuration}")
    lines += [f"- {name.capitalize()}: {value}" for name, value in results.setup.items()]
    lines += [
        f"- As of: {results.as_of.isoformat()}",
        f"- Ingest: ingested {ingest.ingested}, unchanged {ingest.unchanged}, "
        f"quarantined {ingest.quarantined}",
        f"- Questions: {overall.questions} ({overall.failed} failed)",
        "",
        "## Overall",
        "",
        f"| {_SCORE_COLUMNS} |",
        "|---|---|---|---|---|",
        f"| {_score_cells(overall)} |",
        "",
        "## By category",
        "",
        f"| Category | {_SCORE_COLUMNS} |",
        "|---|---|---|---|---|---|",
    ]
    lines += [f"| {_cell(name)} | {_score_cells(s)} |" for name, s in results.categories.items()]
    if results.librarian is not None:
        lines += ["", *_librarian_section(results.librarian, results.injection_checks)]
    lines += [
        "",
        "## Efficiency by category",
        "",
        "Medians over the answers that report them, and their total cost (n/a: none do).",
        "",
        "| Category | Median cost | Median turns | Median duration | Total cost |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {_cell(name)} | {_efficiency_cells(s)} |" for name, s in results.categories.items()
    ]
    lines += [
        f"| Overall | {_efficiency_cells(overall)} |",
        "",
        "## By question",
        "",
        "| Question | Category | Status | Correct | Evidence recall | Citation validity "
        "| Cost | Turns | Duration |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {_cell(q.id)} | {_cell(q.category)} | {q.status} | {_yes_no(q.correct)} | "
        f"{format_score(q.evidence_recall)} | {format_score(q.citation_validity)} | "
        f"{format_cost(q.cost_usd)} | {format_turns(q.turns)} | {format_duration(q.duration_ms)} |"
        for q in results.questions
    ]
    lines += ["", "## Answers"]
    for question in results.questions:
        lines += ["", *_answer_section(question)]
    return "\n".join(lines) + "\n"


def _librarian_section(run: LibrarianRun, checks: InjectionChecks | None) -> list[str]:
    episodes = run.episodes
    lines = [
        "## Claims graph",
        "",
        "Built for this run by the librarian from its L0, with the cache kept between runs.",
        "",
        f"- Extractor version: {run.extractor_version}",
        f"- Models: {', '.join(f'{stage} {model}' for stage, model in run.models.items())}",
        f"- Episodes: {episodes.extracted} extracted, {episodes.skipped} skipped by triage, "
        f"{episodes.superseded} superseded by a later note version, of {episodes.in_l0} in L0",
        f"- Statements: {run.statements.kept} kept, "
        f"{run.statements.dropped_quote_not_found} dropped (quote not found)",
        f"- Built in {run.duration_seconds:.1f} s",
        f"- Injection checks: {_injection_text(checks)}",
        "",
        "| Stage | Cache hits | Cache misses | Model calls |",
        "|---|---|---|---|",
    ]
    stages = [*run.cache, *(stage for stage in run.model_calls if stage not in run.cache)]
    for stage in stages:
        cache = run.cache.get(stage)
        calls = run.model_calls.get(stage, {})
        call_text = ", ".join(f"{model}: {n}" for model, n in sorted(calls.items())) or "none"
        lines.append(
            f"| {_cell(stage)} | {cache.hits if cache else 'n/a'} | "
            f"{cache.misses if cache else 'n/a'} | {_cell(call_text)} |"
        )
    return lines


def _injection_text(checks: InjectionChecks | None) -> str:
    if checks is None:
        return "not run"
    if not checks.adversarial_episodes:
        return "no adversarial episodes listed in the eval set"
    count = len(checks.adversarial_episodes)
    return (
        f"passed, over {count} adversarial episode{'s' if count > 1 else ''} "
        f"({'; '.join(checks.checks)})"
    )


def _answer_section(q: QuestionResult) -> list[str]:
    lines = [
        f"### {q.id} ({q.category})",
        "",
        f"- Question: {q.question}",
        f"- As of: {q.as_of.isoformat()}",
        f"- Expected: {q.expected_answer}",
        f"- Required evidence: {_ids(q.evidence)}",
        f"- Cited: {_ids(q.cited)}",
    ]
    if (q.cost_usd, q.turns, q.duration_ms) != (None, None, None):
        lines.append(
            f"- Cost: {format_cost(q.cost_usd)}, {format_turns(q.turns)} turns, "
            f"{format_duration(q.duration_ms)}"
        )
    if q.unknown_citations:
        lines.append(f"- Not in L0: {_ids(q.unknown_citations)}")
    if q.irrelevant_citations:
        lines.append(f"- Judged irrelevant: {_ids(q.irrelevant_citations)}")
    if q.error is not None:
        lines.append(f"- Failed: {q.error}")
    if q.judge_reasoning is not None:
        lines.append(f"- Judge ({'correct' if q.correct else 'incorrect'}): {q.judge_reasoning}")
    if q.answer is not None:
        lines += ["", "Answer:", ""]
        lines += [f"> {line}".rstrip() for line in q.answer.splitlines() or [""]]
    return lines


def _score_cells(scores: Scores) -> str:
    return (
        f"{scores.questions} | {scores.failed} | {format_score(scores.correctness)} | "
        f"{format_score(scores.evidence_recall)} | {format_score(scores.citation_validity)}"
    )


def _efficiency_cells(scores: Scores) -> str:
    return (
        f"{format_cost(scores.median_cost_usd)} | {format_turns(scores.median_turns)} | "
        f"{format_duration(scores.median_duration_ms)} | {format_cost(scores.total_cost_usd)}"
    )


def format_score(value: float | None) -> str:
    """A score to two decimals, or n/a where it does not apply."""
    return "n/a" if value is None else f"{value:.2f}"


def format_cost(usd: float | None) -> str:
    """A cost in dollars to three decimals (an answer costs cents), or n/a if unknown."""
    return "n/a" if usd is None else f"${usd:.3f}"


def format_turns(turns: float | None) -> str:
    """A number of turns, with a decimal only where a median falls between two."""
    return "n/a" if turns is None else f"{turns:g}"


def format_duration(ms: float | None) -> str:
    """A duration in seconds to one decimal, or n/a if unknown."""
    return "n/a" if ms is None else f"{ms / 1000:.1f} s"


def _yes_no(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def _ids(ids: list[str]) -> str:
    return ", ".join(ids) if ids else "none"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
