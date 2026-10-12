"""`rebuild`: every stage of the librarian, from the episodes in L0 to a published build.

The stages:

1. Triage: mail is kept or skipped by its Gmail category, with a cached relevance call for
   Updates and Forums (see `triage`). Daily notes and chats are always kept; calendar episodes
   are not extracted yet.
2. Extraction: one model call per kept episode, with the earlier episodes of its thread
   as context, cached, on a small worker pool.
3. Assembly: people resolved by address, firsthand speakers from metadata, quotes checked.
4. Build and publish, through the `GraphBuilder`.

A model call that fails (after the model client's retries) fails the rebuild before anything
is built, and every triage answer and extraction finished so far stays cached for the next run.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from functools import partial

from pa_core.envelope import Envelope, Kind
from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.librarian import extraction, triage
from pa_core.librarian.assemble import assemble
from pa_core.librarian.extraction import Extraction
from pa_core.librarian.ports import (
    BackendFor,
    GraphBuilder,
    LibrarianCache,
    LibrarianModels,
    ModelChoice,
)
from pa_core.librarian.report import (
    BuildReport,
    CacheCounts,
    EpisodeCounts,
    StatementCounts,
    TriageCounts,
)
from pa_core.model_client import ModelBackend, ModelClient, ModelRequest
from pa_core.owner import Owner

DEFAULT_WORKERS = 4


class LibrarianError(PaError):
    """A rebuild that could not finish; nothing was published."""


def rebuild(
    l0: L0Store,
    owner: Owner,
    *,
    models: LibrarianModels,
    backend_for: BackendFor,
    cache: LibrarianCache,
    builder: GraphBuilder,
    workers: int = DEFAULT_WORKERS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BuildReport:
    started_at, started = clock(), time.monotonic()
    calls = _CallLog()
    envelopes = sorted(l0.envelopes(), key=_time_order)
    handled = [envelope for envelope in envelopes if envelope.kind in _EXTRACTED_KINDS]

    triager = _Triager(models.default, backend_for, calls, cache, owner)
    outcomes = triager.triage_all(handled, workers)
    selected = [envelope for envelope in handled if outcomes[envelope.episode_id].extracted]

    extractor = _Extractor(models.extraction, backend_for, calls, cache, owner)
    extractions = extractor.extract_all(selected, workers)

    assembled = assemble(selected, extractions, owner)
    assembled.records.validate()
    published = builder.build(assembled.records)

    tally = assembled.statements
    return BuildReport(
        build_id=published.build_id,
        started_at=started_at,
        duration_seconds=round(time.monotonic() - started, 3),
        extractor_version=extractor.version,
        models={"default": str(models.default), extraction.STAGE: str(models.extraction)},
        episodes=EpisodeCounts(
            in_l0=len(envelopes),
            extracted=len(extractions),
            skipped=len(handled) - len(selected),
            not_extracted=len(envelopes) - len(handled),
            triage=_triage_counts(outcomes.values()),
        ),
        cache={triage.STAGE: triager.cache_counts, extraction.STAGE: extractor.cache_counts},
        model_calls=calls.by_stage(),
        statements=StatementCounts(
            extracted=tally.extracted,
            kept=tally.kept,
            dropped_quote_not_found=tally.dropped_quote_not_found,
        ),
        nodes=published.node_counts,
        edges=published.edge_counts,
    )


# Calendar episodes reach the graph through their own deterministic stage, not extraction.
_EXTRACTED_KINDS = frozenset({Kind.EMAIL, Kind.NOTE, Kind.CHAT})


def _time_order(envelope: Envelope) -> tuple[datetime, str]:
    return (envelope.occurred_at.start, envelope.episode_id)


def _triage_counts(outcomes: Iterable[triage.Outcome]) -> TriageCounts:
    counted = Counter(outcomes)
    return TriageCounts(
        primary=counted[triage.Outcome.PRIMARY],
        skipped_by_category=counted[triage.Outcome.SKIPPED_BY_CATEGORY],
        relevant=counted[triage.Outcome.RELEVANT],
        not_relevant=counted[triage.Outcome.NOT_RELEVANT],
    )


class _Triager:
    def __init__(
        self,
        choice: ModelChoice,
        backend_for: BackendFor,
        calls: _CallLog,
        cache: LibrarianCache,
        owner: Owner,
    ) -> None:
        self.version = triage.triage_version(choice)
        self.cache_counts = CacheCounts()
        self._client = ModelClient(calls.counting(triage.STAGE, choice, backend_for(choice)))
        self._cache = cache
        self._owner = owner
        self._lock = threading.Lock()

    def triage_all(self, episodes: Sequence[Envelope], workers: int) -> dict[str, triage.Outcome]:
        """Each episode's outcome, by episode id; raises LibrarianError if any call fails."""
        outcomes = {
            envelope.episode_id: triage.outcome_without_check(envelope)
            for envelope in episodes
            if not triage.needs_check(envelope)
        }
        checked = [envelope for envelope in episodes if triage.needs_check(envelope)]
        jobs = [(envelope, partial(self._check, envelope)) for envelope in checked]
        return outcomes | _in_pool(jobs, workers, triage.STAGE)

    def _check(self, envelope: Envelope) -> triage.Outcome:
        key = triage.cache_key(envelope, self.version)
        cached = self._cache.get(triage.STAGE, key)
        with self._lock:
            if cached is None:
                self.cache_counts.misses += 1
            else:
                self.cache_counts.hits += 1
        if cached is not None:
            answer = triage.Relevance.model_validate_json(cached)
        else:
            prompt = triage.relevance_prompt(envelope, self._owner)
            answer = self._client.generate(prompt, triage.Relevance)
            self._cache.put(triage.STAGE, key, answer.model_dump_json())
        return triage.Outcome.RELEVANT if answer.relevant else triage.Outcome.NOT_RELEVANT


class _Extractor:
    def __init__(
        self,
        choice: ModelChoice,
        backend_for: BackendFor,
        calls: _CallLog,
        cache: LibrarianCache,
        owner: Owner,
    ) -> None:
        self.version = extraction.extractor_version(choice)
        self.cache_counts = CacheCounts()
        self._client = ModelClient(calls.counting(extraction.STAGE, choice, backend_for(choice)))
        self._cache = cache
        self._owner = owner
        self._lock = threading.Lock()

    def extract_all(self, episodes: Sequence[Envelope], workers: int) -> dict[str, Extraction]:
        """Each episode's extraction, by episode id; raises LibrarianError if any call fails."""
        threads: defaultdict[str | None, list[Envelope]] = defaultdict(list)
        jobs: list[tuple[Envelope, list[Envelope]]] = []
        for envelope in episodes:  # in time order, so each thread's list is its earlier ones
            earlier = threads[envelope.thread_ref] if envelope.thread_ref else []
            jobs.append((envelope, extraction.thread_context(earlier)))
            if envelope.thread_ref:
                threads[envelope.thread_ref].append(envelope)

        work = [(envelope, partial(self._extract, envelope, context)) for envelope, context in jobs]
        return _in_pool(work, workers, extraction.STAGE)

    def _extract(self, envelope: Envelope, context: Sequence[Envelope]) -> Extraction:
        key = extraction.cache_key(envelope, context, self.version)
        cached = self._cache.get(extraction.STAGE, key)
        with self._lock:
            if cached is None:
                self.cache_counts.misses += 1
            else:
                self.cache_counts.hits += 1
        if cached is not None:
            return Extraction.model_validate_json(cached)
        prompt = extraction.extraction_prompt(envelope, context, self._owner)
        result = self._client.generate(prompt, Extraction)
        self._cache.put(extraction.STAGE, key, result.model_dump_json())
        return result


def _in_pool[T](
    jobs: Sequence[tuple[Envelope, Callable[[], T]]], workers: int, stage: str
) -> dict[str, T]:
    """Each job's result, by its episode's id, run on a small pool.

    On the first failure the jobs not yet started are cancelled, the ones running finish (and
    cache what they made), and LibrarianError is raised, so nothing is built.
    """
    results: dict[str, T] = {}
    failures: list[tuple[Envelope, PaError]] = []
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=stage)
    try:
        futures: dict[Future[T], Envelope] = {pool.submit(job): envelope for envelope, job in jobs}
        for future in as_completed(futures):
            envelope = futures[future]
            if future.cancelled():  # after a failure: never started, so nothing to keep
                continue
            try:
                results[envelope.episode_id] = future.result()
            except PaError as error:
                failures.append((envelope, error))
                for pending in futures:
                    pending.cancel()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if failures:
        envelope, error = failures[0]
        raise LibrarianError(
            f"{stage} failed for {len(failures)} episode(s), so nothing was published; "
            f"the {len(results)} finished are cached for the next run. "
            f"First failure, {envelope.episode_id}: {error}"
        ) from error
    return results


class _CallLog:
    """Model calls by stage and model, counted at the backend so retries count too."""

    def __init__(self) -> None:
        self._counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._lock = threading.Lock()

    def counting(self, stage: str, choice: ModelChoice, backend: ModelBackend) -> ModelBackend:
        log = self

        class _Counting:
            def complete(self, request: ModelRequest) -> str:
                with log._lock:
                    log._counts[stage][choice.model] += 1
                return backend.complete(request)

        return _Counting()

    def by_stage(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {stage: dict(counts) for stage, counts in self._counts.items()}
