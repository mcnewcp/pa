"""The results of an eval run, as written to `results.json`."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from pa_core.librarian.report import BuildReport, CacheCounts, EpisodeCounts, StatementCounts
from pa_evals.eval_set import Category


class QuestionResult(BaseModel):
    """One question's answer and scores.

    A failed question (the agent or the judge raised) has `status` "failed", the error, and
    whatever scores could still be worked out. A score is None where it does not apply: recall
    for a question with no required evidence, validity for an answer with no citations. Cost,
    turns, and duration are None where the agent didn't report them, and in results written
    before they were recorded.
    """

    id: str
    category: Category
    question: str
    as_of: datetime
    expected_answer: str
    evidence: list[str]
    status: Literal["scored", "failed"]
    error: str | None = None
    answer: str | None = None
    cited: list[str] = []
    unknown_citations: list[str] = []
    """Cited ids that are not in the catalog."""
    irrelevant_citations: list[str] = []
    """Cited ids that exist but the judge found irrelevant."""
    evidence_recall: float | None = None
    citation_validity: float | None = None
    correct: bool | None = None
    judge_reasoning: str | None = None
    cost_usd: float | None = None
    """What the agent's session cost, in US dollars at API prices."""
    turns: int | None = None
    duration_ms: int | None = None


class Scores(BaseModel):
    """Scores rolled up over a group of questions.

    Correctness is the share of questions judged correct, so a failed question counts as wrong.
    Evidence recall and citation validity are means over the questions where they apply; a
    question whose agent failed cited nothing, so its recall is 0.

    Cost, turns, and duration are summarised over the answers that report them: the median of
    each, and the total cost. They are None where no answer does, as in results written before
    they were recorded.
    """

    questions: int
    failed: int
    correctness: float
    evidence_recall: float | None
    citation_validity: float | None
    median_cost_usd: float | None = None
    median_turns: float | None = None
    median_duration_ms: float | None = None
    total_cost_usd: float | None = None

    @classmethod
    def over(cls, results: Sequence[QuestionResult]) -> Scores:
        costs = [r.cost_usd for r in results if r.cost_usd is not None]
        return cls(
            questions=len(results),
            failed=sum(r.status == "failed" for r in results),
            correctness=_share([r.correct is True for r in results]) or 0.0,
            evidence_recall=_mean([r.evidence_recall for r in results]),
            citation_validity=_mean([r.citation_validity for r in results]),
            median_cost_usd=median(costs),
            median_turns=median([r.turns for r in results]),
            median_duration_ms=median([r.duration_ms for r in results]),
            total_cost_usd=sum(costs) if costs else None,
        )


class IngestCounts(BaseModel):
    ingested: int
    unchanged: int
    quarantined: int


class LibrarianRun(BaseModel):
    """What building the run's claims graph did and cost: its build report, less the build's
    id and start time."""

    extractor_version: str
    models: dict[str, str]
    """The model (and effort) of each stage."""
    duration_seconds: float
    episodes: EpisodeCounts
    cache: dict[str, CacheCounts]
    """Hits and misses by stage."""
    model_calls: dict[str, dict[str, int]]
    """Calls by stage, then by model."""
    statements: StatementCounts
    nodes: dict[str, int]
    edges: dict[str, int]

    @classmethod
    def of(cls, report: BuildReport) -> LibrarianRun:
        return cls.model_validate(report.model_dump(exclude={"build_id", "started_at"}))


class InjectionChecks(BaseModel):
    """The injection checks a run's graph passed (a failed one stops the run)."""

    adversarial_episodes: list[str]
    checks: list[str]
    """The rules checked."""


class RunResults(BaseModel):
    configuration: str | None = None
    """The configuration the run asked, which keys its results in a comparison. None in results
    written before configurations were named."""
    setup: dict[str, str] = {}
    """How the run was set up (for example which agent runner and judge), for the report."""
    librarian: LibrarianRun | None = None
    """The build of the claims graph the agent was given; None when it had files alone."""
    injection_checks: InjectionChecks | None = None
    """The injection checks run against that build; None when there was none."""
    as_of: datetime
    """The eval set's default as-of time."""
    ingest: IngestCounts
    overall: Scores
    categories: dict[Category, Scores]
    """Scores per category, in the order categories first appear in the eval set."""
    questions: list[QuestionResult]
    """Per-question results, in eval set order."""


def _share(flags: Sequence[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def median(values: Sequence[float | None]) -> float | None:
    """The median of the values that are present, or None if none are."""
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None
