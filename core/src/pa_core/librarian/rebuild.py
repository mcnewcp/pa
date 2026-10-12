"""`rebuild`: every stage of the librarian, from the episodes in L0 to a published build.

The stages:

1. Triage: mail is kept or skipped by its Gmail category, with a cached relevance call for
   Updates and Forums (see `triage`). Daily notes, chats, and calendar events are always kept.
2. Extraction: one model call per kept episode, with the earlier episodes of its thread
   as context, cached, on a small worker pool.
3. Assembly, in time order: people and organizations resolved (a model call, cached, only
   for a name the records can't decide), speakers attributed, quotes checked.
4. Topics: the kept statements' topic names placed under topics, in time order (`topics.py`).
5. Calendar events: each event and its versions, from its episodes alone (`calendar.py`).
6. Build and publish, through the `GraphBuilder`.

Episodes are taken in time order: when they happened, or for a version of a calendar event,
when that version was made (an event's own time may be months later, or moved earlier).

A model call that fails (after the model client's retries) fails the rebuild before anything
is built, and every triage answer, extraction, identification, and topic decision finished so
far stays cached for the next run.
"""

from __future__ import annotations

import functools
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from functools import partial

from pa_core.claims.records import EdgeRecord
from pa_core.claims.schema import ABOUT
from pa_core.envelope import Envelope, Kind
from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.librarian import calendar, extraction, identify, topics, triage
from pa_core.librarian.assemble import Assembled, assemble, statement_id
from pa_core.librarian.extraction import Extraction
from pa_core.librarian.identify import Identifier
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
    backend_for = functools.cache(backend_for)  # one backend per model choice, shared by stages
    loaded = list(l0.envelopes())
    versions = calendar.read_versions(loaded, l0)
    known_at = {eid: version.changed_at for eid, version in versions.items()}
    envelopes = sorted(loaded, key=lambda e: _time_order(e, known_at))
    handled = [envelope for envelope in envelopes if envelope.kind in _EXTRACTED_KINDS]

    triager = _Triager(models.default, backend_for, calls, cache, owner)
    outcomes = triager.triage_all(handled, workers)
    selected = [envelope for envelope in handled if outcomes[envelope.episode_id].extracted]

    extractor = _Extractor(models.extraction, backend_for, calls, cache, owner)
    extractions = extractor.extract_all(selected, workers)

    quotable = {e.episode_id: extraction.quotable(e, l0) for e in selected}
    identifier = Identifier(
        models.default,
        ModelClient(calls.counting(identify.STAGE, models.default, backend_for(models.default))),
        cache,
        owner,
    )
    try:
        assembled = assemble(selected, extractions, owner, quotable, identifier, said_at=known_at)
    except PaError as error:
        raise LibrarianError(
            f"resolution failed, so nothing was published; every extraction and decision "
            f"made so far is cached for the next run: {error}"
        ) from error
    resolver = topics.TopicResolver(
        ModelClient(calls.counting(topics.STAGE, models.default, backend_for(models.default))),
        cache,
        topics.resolver_version(models.default),
    )
    try:
        resolved = resolver.resolve(_topic_inputs(selected, extractions, assembled))
    except PaError as error:
        raise LibrarianError(
            "topic resolution failed, so nothing was published; the decisions made so far "
            f"are cached for the next run: {error}"
        ) from error
    assembled.records.nodes.extend(resolved.nodes)
    assembled.records.edges.extend(resolved.edges)
    events, event_edges = calendar.event_records(
        (versions[e.episode_id] for e in selected if e.episode_id in versions),
        _topics_by_episode(resolved.edges),
    )
    assembled.records.nodes.extend(events)
    assembled.records.edges.extend(event_edges)
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
        cache={
            triage.STAGE: triager.cache_counts,
            extraction.STAGE: extractor.cache_counts,
            identify.STAGE: identifier.cache_counts,
            topics.STAGE: resolver.cache_counts,
        },
        model_calls=calls.by_stage(),
        statements=StatementCounts(
            extracted=tally.extracted,
            kept=tally.kept,
            dropped_quote_not_found=tally.dropped_quote_not_found,
        ),
        nodes=published.node_counts,
        edges=published.edge_counts,
    )


_EXTRACTED_KINDS = frozenset({Kind.EMAIL, Kind.NOTE, Kind.CHAT, Kind.CALENDAR_EVENT})


def _time_order(envelope: Envelope, known_at: Mapping[str, datetime]) -> tuple[datetime, str]:
    eid = envelope.episode_id
    return (known_at.get(eid, envelope.occurred_at.start), eid)


def _topics_by_episode(edges: Iterable[EdgeRecord]) -> dict[str, list[str]]:
    """The topics each episode's statements are about, from the statements' ABOUT edges."""
    by_episode: defaultdict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.label != ABOUT.label:
            continue
        for episode in edge.episode_ids:
            if edge.target not in by_episode[episode]:
                by_episode[episode].append(edge.target)
    return dict(by_episode)


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


def _topic_inputs(
    episodes: Sequence[Envelope], extractions: dict[str, Extraction], assembled: Assembled
) -> list[topics.EpisodeTopics]:
    """The kept statements of `episodes` (in time order) that name topics, by episode."""
    kept = {node.id for node in assembled.records.nodes}
    inputs = []
    for envelope in episodes:
        found = extractions.get(envelope.episode_id)
        if found is None:
            continue
        statements = [
            topics.StatementTopics(sid, statement.summary, statement.topics)
            for position, statement in enumerate(found.statements)
            if statement.topics and (sid := statement_id(envelope.episode_id, position)) in kept
        ]
        if statements:
            inputs.append(
                topics.EpisodeTopics(
                    envelope.episode_id, envelope.thread_ref, envelope.subject, statements
                )
            )
    return inputs


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
