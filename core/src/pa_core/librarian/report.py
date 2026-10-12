"""The build report: what a rebuild did and what it cost."""

from __future__ import annotations

from collections import Counter

from pydantic import AwareDatetime, BaseModel


class CacheCounts(BaseModel):
    hits: int = 0
    misses: int = 0


class EpisodeCounts(BaseModel):
    in_l0: int = 0
    extracted: int = 0
    """Episodes whose statements are in the graph, from the cache or a model call."""
    not_extracted: int = 0
    """Episodes left out because no stage handles their kind yet."""


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
                f"episodes: {self.episodes.extracted} extracted of {self.episodes.in_l0} in L0",
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
