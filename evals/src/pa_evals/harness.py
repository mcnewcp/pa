"""The eval harness: build a throwaway PA from the corpus, ask the eval set, and score it."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pa_core.catalog import open_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest, is_hidden
from pa_core.l0 import L0Store
from pa_core.owner import Owner
from pa_evals.agent import AgentRequest, AgentRunner
from pa_evals.citations import opened, parse_citations
from pa_evals.configurations import DEFAULT_CONFIGURATION, Configuration
from pa_evals.eval_set import Category, EvalQuestion, EvalSet
from pa_evals.judge import Judge, JudgeRequest
from pa_evals.results import IngestCounts, QuestionResult, RunResults, Scores, ToolCallResult
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0

DEFAULT_CONCURRENCY = 4


class CorpusCopyError(PaError):
    """A corpus directory that cannot be copied into the inbox."""


def run_eval(
    corpus: Path | Sequence[Path],
    eval_set: EvalSet,
    agent: AgentRunner,
    judge: Judge,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    configuration: Configuration = DEFAULT_CONFIGURATION,
    setup: Mapping[str, str] | None = None,
) -> RunResults:
    """Ingest `corpus` into a fresh throwaway data root, then ask and score every question.

    Every file under `corpus` (hidden files aside) is a raw payload, laid out as the inbox is:
    one directory per source (ADR-0004). It is copied into the inbox as it is. A corpus in
    several directories (the scale corpus: Argus's payloads and the extension's) is their
    union; a payload path in more than one of them is refused. The data root is
    created in the system temporary directory, never the configured `PA_DATA_DIR`, and deleted
    once the run ends. Questions run `concurrency` at a time, one pass each. A question whose
    assistant or judge raises is recorded as failed and the run goes on.

    `configuration` says what the assistant is given, and is recorded in the results. `setup`
    describes the run (for example which agent runner and judge) and is carried into the
    results as it is.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    owner = eval_set.owner.to_owner()
    with tempfile.TemporaryDirectory(prefix="pa-eval-") as directory:
        data_root = DataRoot(Path(directory))
        _copy_into_inbox(corpus, data_root.inbox)
        store = FilesystemL0(data_root.l0)
        with open_catalog(data_root.catalog, store) as catalog:
            # Captured "at" the as-of time, so nothing in L0 shows the real date to the agent.
            summary = ingest(data_root.inbox, store, catalog, owner, now=lambda: eval_set.as_of)
            known = {entry.episode_id for entry in catalog.entries()}

        def ask(question: EvalQuestion) -> QuestionResult:
            return _ask(question, eval_set, data_root, owner, store, known, agent, judge)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            questions = list(pool.map(ask, eval_set.questions))

    categories: dict[Category, list[QuestionResult]] = {}
    for result in questions:
        categories.setdefault(result.category, []).append(result)
    return RunResults(
        configuration=configuration.name,
        setup=dict(setup or {}),
        as_of=eval_set.as_of,
        ingest=IngestCounts(
            ingested=summary.ingested, unchanged=summary.unchanged, quarantined=summary.quarantined
        ),
        overall=Scores.over(questions),
        categories={name: Scores.over(results) for name, results in categories.items()},
        questions=questions,
    )


def _ask(
    question: EvalQuestion,
    eval_set: EvalSet,
    data_root: DataRoot,
    owner: Owner,
    store: L0Store,
    known: set[str],
    agent: AgentRunner,
    judge: Judge,
) -> QuestionResult:
    as_of = eval_set.as_of_for(question)
    result = QuestionResult(
        id=question.id,
        category=question.category,
        question=question.question,
        as_of=as_of,
        expected_answer=question.expected_answer,
        evidence=question.evidence,
        status="failed",
    )
    request = AgentRequest(question.id, question.question, as_of, data_root, owner)
    answer = _attempt(lambda: agent.answer(request))
    if isinstance(answer, _Failure):
        recall = 0.0 if question.evidence else None
        return result.model_copy(update={"error": f"agent: {answer}", "evidence_recall": recall})

    cited = parse_citations(answer.text)
    calls = answer.tool_calls
    existing = [episode_id for episode_id in cited if episode_id in known]
    result = result.model_copy(
        update={
            "answer": answer.text,
            "cost_usd": answer.cost_usd,
            "turns": answer.turns,
            "duration_ms": answer.duration_ms,
            "tool_calls": None
            if calls is None
            else [
                ToolCallResult(tool=c.tool, input=dict(c.input), errored=c.errored) for c in calls
            ],
            "cited": cited,
            "cited_opened": None if calls is None else opened(cited, calls),
            "unknown_citations": [episode_id for episode_id in cited if episode_id not in known],
            "evidence_recall": _evidence_recall(question.evidence, cited),
        }
    )
    episodes = tuple(e for e in (store.get(i) for i in existing) if e is not None)
    verdict = _attempt(
        lambda: judge.judge(
            JudgeRequest(
                question_id=question.id,
                question=question.question,
                expected_answer=question.expected_answer,
                as_of=as_of,
                answer=answer.text,
                cited_episodes=episodes,
            )
        )
    )
    if isinstance(verdict, _Failure):
        return result.model_copy(update={"error": f"judge: {verdict}"})

    relevant = set(verdict.relevant_citations)
    valid = [episode_id for episode_id in existing if episode_id in relevant]
    return result.model_copy(
        update={
            "status": "scored",
            "irrelevant_citations": [i for i in existing if i not in relevant],
            "citation_validity": len(valid) / len(cited) if cited else None,
            "correct": verdict.correct,
            "judge_reasoning": verdict.reasoning,
        }
    )


def _evidence_recall(required: list[str], cited: list[str]) -> float | None:
    if not required:
        return None
    return sum(episode_id in cited for episode_id in required) / len(required)


class _Failure:
    def __init__(self, error: Exception) -> None:
        self.message = (
            str(error) if isinstance(error, PaError) else f"{type(error).__name__}: {error}"
        )

    def __str__(self) -> str:
        return self.message


def _attempt[T](call: Callable[[], T]) -> T | _Failure:
    """The call's result, or the failure it raised; one question never stops the run."""
    try:
        return call()
    except Exception as error:
        return _Failure(error)


def _copy_into_inbox(corpus: Path | Sequence[Path], inbox: Path) -> None:
    directories = [corpus] if isinstance(corpus, Path) else list(corpus)
    inbox.mkdir(parents=True)
    for directory in directories:
        if not directory.is_dir():
            raise CorpusCopyError(f"corpus directory not found: {directory}")
        for payload in sorted(directory.rglob("*")):
            relative = payload.relative_to(directory)
            if not payload.is_file() or is_hidden(relative):
                continue
            target = inbox / relative
            if target.exists():
                raise CorpusCopyError(
                    f"{relative.as_posix()} is in more than one of the corpus's directories"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(payload, target)
