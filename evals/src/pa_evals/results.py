"""The results of an eval run, as written to `results.json`."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from pa_evals.eval_set import Category


class ToolCallResult(BaseModel):
    """One tool call the assistant's session made on its way to an answer."""

    tool: str
    input: dict[str, Any]
    errored: bool


class QuestionResult(BaseModel):
    """One question's answer and scores.

    A failed question (the agent or the judge raised) has `status` "failed", the error, and
    whatever scores could still be worked out. A score is None where it does not apply: recall
    for a question with no required evidence, validity for an answer with no citations. Cost,
    turns, and duration are None where the agent didn't report them, and in results written
    before they were recorded. Tool calls are None where they weren't recorded (a scripted
    answer, or results written before they were).
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
    tool_calls: list[ToolCallResult] | None = None
    """The session's tool calls, in the order it made them."""
    cited_opened: list[str] | None = None
    """Cited ids whose episode file the session read; None where tool calls weren't recorded."""


class Scores(BaseModel):
    """Scores rolled up over a group of questions.

    Correctness is the share of questions judged correct, so a failed question counts as wrong.
    Evidence recall and citation validity are means over the questions where they apply; a
    question whose agent failed cited nothing, so its recall is 0.

    Cost, turns, and duration are summarised over the answers that report them: the median of
    each, and the total cost. They are None where no answer does, as in results written before
    they were recorded.

    The share of cited episodes opened pools the citations of every answer whose tool calls
    were recorded: of all they cite, the share their sessions read. It is None where none cites
    anything with its tool calls recorded.
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
    cited_opened: float | None = None

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
            cited_opened=cited_opened(results),
        )


class IngestCounts(BaseModel):
    ingested: int
    unchanged: int
    quarantined: int


class WikiResult(BaseModel):
    """The eval wiki a files-plus-wiki run gave the assistant."""

    librarian_version: str
    """The librarian version that built it, from its run log."""


class InjectionChecks(BaseModel):
    """The injection checks a run's eval wiki passed before any question was asked."""

    adversarial_episodes: list[str]
    """The adversarial episodes checked against, as the eval set lists them; empty for a corpus
    with none, such as Argus."""
    checks: list[str]
    """The rules checked."""


class RunResults(BaseModel):
    configuration: str | None = None
    """The configuration the run asked, which keys its results in a comparison. None in results
    written before configurations were named."""
    setup: dict[str, str] = {}
    """How the run was set up (for example which agent runner and judge), for the report."""
    wiki: WikiResult | None = None
    """The eval wiki the assistant was given; None in files alone, and in results written
    before configurations had a wiki."""
    injection_checks: InjectionChecks | None = None
    """The injection checks its eval wiki passed; None where the assistant had no wiki. v0.12's
    claims-graph runs recorded their checks in this same shape."""
    as_of: datetime
    """The eval set's default as-of time."""
    ingest: IngestCounts
    overall: Scores
    categories: dict[Category, Scores]
    """Scores per category, in the order categories first appear in the eval set."""
    questions: list[QuestionResult]
    """Per-question results, in eval set order."""


def cited_opened(results: Sequence[QuestionResult]) -> float | None:
    """Of the citations in answers whose tool calls were recorded, the share their sessions
    opened, or None if there are none."""
    recorded = [r for r in results if r.cited_opened is not None]
    cited = sum(len(r.cited) for r in recorded)
    return sum(len(r.cited_opened or []) for r in recorded) / cited if cited else None


def _share(flags: Sequence[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def median(values: Sequence[float | None]) -> float | None:
    """The median of the values that are present, or None if none are."""
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None
