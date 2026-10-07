"""Writing an eval run to disk: `report.md` and `results.json` in a timestamped run directory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pa_evals.results import QuestionResult, RunResults, Scores

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
    run_dir = _new_run_dir(runs_dir, now().astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ"))
    (run_dir / "results.json").write_text(results.model_dump_json(indent=2) + "\n")
    (run_dir / "report.md").write_text(render_report(results))
    return run_dir


def _new_run_dir(runs_dir: Path, name: str) -> Path:
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
    lines += [f"- {name.capitalize()}: {value}" for name, value in results.setup.items()]
    lines += [
        f"- As of: {results.as_of.isoformat()}",
        f"- Corpus: ingested {ingest.ingested}, unchanged {ingest.unchanged}, "
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
    lines += [
        "",
        "## By question",
        "",
        "| Question | Category | Status | Correct | Evidence recall | Citation validity |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {_cell(q.id)} | {_cell(q.category)} | {q.status} | {_yes_no(q.correct)} | "
        f"{_number(q.evidence_recall)} | {_number(q.citation_validity)} |"
        for q in results.questions
    ]
    lines += ["", "## Answers"]
    for question in results.questions:
        lines += ["", *_answer_section(question)]
    return "\n".join(lines) + "\n"


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
        f"{scores.questions} | {scores.failed} | {_number(scores.correctness)} | "
        f"{_number(scores.evidence_recall)} | {_number(scores.citation_validity)}"
    )


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _yes_no(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def _ids(ids: list[str]) -> str:
    return ", ".join(ids) if ids else "none"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")
