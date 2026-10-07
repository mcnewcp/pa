"""The results of an eval run, as written to `results.json`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from pa_evals.eval_set import Category


class QuestionResult(BaseModel):
    """One question's answer and scores.

    A failed question (the agent or the judge raised) has `status` "failed", the error, and
    whatever scores could still be worked out. A score is None where it does not apply: recall
    for a question with no required evidence, validity for an answer with no citations.
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


class Scores(BaseModel):
    """Scores rolled up over a group of questions.

    Correctness is the share of questions judged correct, so a failed question counts as wrong.
    Evidence recall and citation validity are means over the questions where they apply; a
    question whose agent failed cited nothing, so its recall is 0.
    """

    questions: int
    failed: int
    correctness: float
    evidence_recall: float | None
    citation_validity: float | None

    @classmethod
    def over(cls, results: Sequence[QuestionResult]) -> Scores:
        return cls(
            questions=len(results),
            failed=sum(r.status == "failed" for r in results),
            correctness=_share([r.correct is True for r in results]) or 0.0,
            evidence_recall=_mean([r.evidence_recall for r in results]),
            citation_validity=_mean([r.citation_validity for r in results]),
        )


class IngestCounts(BaseModel):
    ingested: int
    unchanged: int
    quarantined: int


class RunResults(BaseModel):
    setup: dict[str, str] = {}
    """How the run was set up (for example which agent runner and judge), for the report."""
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
