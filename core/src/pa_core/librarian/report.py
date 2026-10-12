"""The build report: what a rebuild did and what it cost."""

from __future__ import annotations

from collections import Counter

from pydantic import AwareDatetime, BaseModel


class CacheCounts(BaseModel):
    hits: int = 0
    misses: int = 0


class TriageCounts(BaseModel):
    """Mail by triage outcome. Skipped mail is left out of the graph but stays in L0."""

    primary: int = 0
    """Primary or uncategorised: extracted without a check."""
    skipped_by_category: int = 0
    """Promotions or Social: skipped without a model call."""
    relevant: int = 0
    """Updates or Forums that the relevance check kept: extracted."""
    not_relevant: int = 0
    """Updates or Forums that the relevance check turned down: skipped."""


class EpisodeCounts(BaseModel):
    in_l0: int = 0
    extracted: int = 0
    """Episodes whose statements are in the graph, from the cache or a model call."""
    skipped: int = 0
    """Mail that triage left out of the graph."""
    not_extracted: int = 0
    """Episodes left out because no stage handles their kind yet."""
    triage: TriageCounts = TriageCounts()


class StatementCounts(BaseModel):
    extracted: int = 0
    kept: int = 0
    dropped_quote_not_found: int = 0


class BuildReport(BaseModel):
    build_id: str
    started_at: AwareDatetime
    duration_seconds: float
    extractor_version: str
    models: dict[str, str]
    """The model (and effort) of each stage."""
    episodes: EpisodeCounts
    cache: dict[str, CacheCounts]
    """Hits and misses by stage."""
    model_calls: dict[str, dict[str, int]]
    """Calls by stage, then by model. A reply retried for invalid output counts twice."""
    statements: StatementCounts
    nodes: dict[str, int]
    edges: dict[str, int]

    def model_calls_by_model(self) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for by_model in self.model_calls.values():
            totals.update(by_model)
        return dict(totals)

    def summary(self) -> str:
        """A few lines for the console."""
        calls = self.model_calls_by_model()
        call_text = ", ".join(f"{n} to {model}" for model, n in sorted(calls.items())) or "none"
        cache_text = "; ".join(
            f"{stage} {c.hits} hits, {c.misses} misses" for stage, c in self.cache.items()
        )
        return "\n".join(
            [
                f"published build {self.build_id} in {self.duration_seconds:.1f} s "
                f"(extractor {self.extractor_version})",
                f"episodes: {self.episodes.extracted} extracted, {self.episodes.skipped} "
                f"skipped by triage, of {self.episodes.in_l0} in L0",
                f"triage: {_counts(self.episodes.triage.model_dump())}",
                f"cache: {cache_text}",
                f"model calls: {call_text}",
                f"statements: {self.statements.kept} kept, "
                f"{self.statements.dropped_quote_not_found} dropped (quote not found)",
                f"nodes: {_counts(self.nodes)}",
                f"edges: {_counts(self.edges)}",
            ]
        )


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{label} {n}" for label, n in counts.items())
