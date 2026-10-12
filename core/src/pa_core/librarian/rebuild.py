"""`rebuild`: every stage of the librarian, from the episodes in L0 to a published build.

The stages:

1. Selection: for now every email is extracted, and nothing else.
2. Extraction: one model call per selected episode, with the earlier episodes of its thread
   as context, cached, on a small worker pool.
3. Assembly: people resolved by address, firsthand speakers from metadata, quotes checked.
4. Build and publish, through the `GraphBuilder`.

A model call that fails (after the model client's retries) fails the rebuild before anything
is built, and every extraction finished so far stays cached for the next run.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from pa_core.envelope import Envelope, Kind
from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.librarian import extraction
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
    selected = [envelope for envelope in envelopes if envelope.kind == Kind.EMAIL]

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
            not_extracted=len(envelopes) - len(selected),
        ),
        cache={extraction.STAGE: extractor.cache_counts},
        model_calls=calls.by_stage(),
        statements=StatementCounts(
            extracted=tally.extracted,
            kept=tally.kept,
            dropped_quote_not_found=tally.dropped_quote_not_found,
        ),
        nodes=published.node_counts,
        edges=published.edge_counts,
    )


def _time_order(envelope: Envelope) -> tuple[datetime, str]:
    return (envelope.occurred_at.start, envelope.episode_id)


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

        results: dict[str, Extraction] = {}
        failures: list[tuple[Envelope, PaError]] = []
        pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="extract")
        try:
            futures: dict[Future[Extraction], Envelope] = {
                pool.submit(self._extract, envelope, context): envelope
                for envelope, context in jobs
            }
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
                f"extraction failed for {len(failures)} episode(s), so nothing was published; "
                f"the {len(results)} finished are cached for the next run. "
                f"First failure, {envelope.episode_id}: {error}"
            ) from error
        return results

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
