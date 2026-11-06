"""The backfill's size, in numbers only (story 87 of #80): how many episodes L0 holds since the
window start, per source and per Gmail category, and what replaying them would cost.

The cost is the episodes a replay from the window start would offer, at the last replay's cost
per offered episode: the replay under the data root's replays directory whose run log changed
last, over its committed runs that reported a cost. Until a replay has run there, it's the
scale corpus's reference replay's rate. Nothing here reads a payload's content.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pa_core.envelope import Kind, Source
from pa_core.librarian.replay import (
    REFERENCE_COST_PER_OFFERED,
    RUN_LOG,
    cost_per_offered,
    replay_scope,
)
from pa_core.selection import Category, category
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0


@dataclass(frozen=True)
class BackfillEstimate:
    window_start: datetime
    by_source: Counter[Source]
    """Episodes since the window start, per source."""
    mail: Counter[Category]
    """Of them, the messages per Gmail category, every category counted from zero."""
    offered: int
    """How many a replay from the window start would offer the librarian."""
    cost_per_offered: float
    rate_from: str | None
    """The replay the rate is from (its directory's name), or None for the reference rate."""

    @property
    def cost(self) -> float:
        return self.offered * self.cost_per_offered


def estimate_backfill(data_root: DataRoot, window_start: datetime) -> BackfillEstimate:
    """The backfill's counts in L0 since `window_start`, and the replay's estimated cost."""
    time_zone = window_start.tzinfo
    assert time_zone is not None, "the window start has its time zone"
    scope = replay_scope(FilesystemL0(data_root.l0), time_zone=time_zone, window_start=window_start)
    mail = Counter(dict.fromkeys(Category, 0))
    mail.update(category(e) for e in scope.taken if e.kind is Kind.EMAIL)
    rate, rate_from = _last_replay_rate(data_root)
    return BackfillEstimate(
        window_start=window_start,
        by_source=Counter(e.source for e in scope.taken),
        mail=mail,
        offered=scope.offered,
        cost_per_offered=rate,
        rate_from=rate_from,
    )


def _last_replay_rate(data_root: DataRoot) -> tuple[float, str | None]:
    """The cost per offered episode of the last replay that has one, and its name."""
    logs = sorted(
        data_root.replays.glob(f"*/{RUN_LOG}"), key=lambda log: log.stat().st_mtime, reverse=True
    )
    for log in logs:
        rate = cost_per_offered(log)
        if rate is not None:
            return rate, log.parent.name
    return REFERENCE_COST_PER_OFFERED, None


def estimate_lines(estimate: BackfillEstimate, time_zone: ZoneInfo) -> list[str]:
    """The estimate as `pa` prints it: counts and dollars only."""
    day = estimate.window_start.astimezone(time_zone).date()
    lines = []
    for source, count in sorted(estimate.by_source.items()):
        line = f"backfill: {source}: {_episodes(count)} since {day}"
        if source is Source.GMAIL:
            line += ": " + ", ".join(f"{c} {n}" for c, n in estimate.mail.items())
        lines.append(line)
    rate_from = (
        "the scale corpus's reference replay"
        if estimate.rate_from is None
        else f"the last replay's, {estimate.rate_from}"
    )
    lines.append(
        f"backfill: a replay from {day} would offer {_episodes(estimate.offered)}: about "
        f"${estimate.cost:.2f}, at ${estimate.cost_per_offered:.3f} per offered episode "
        f"({rate_from})"
    )
    return lines


def _episodes(count: int) -> str:
    return f"{count} episode" if count == 1 else f"{count} episodes"
