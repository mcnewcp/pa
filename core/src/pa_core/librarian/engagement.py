"""Engagement: which slugs the owner has had a hand in, from L0's metadata alone
(`pa_core.librarian.metadata`, which says whose doing each episode is).

A slug is engaged when an observation linking it cites an episode whose sender, author,
organizer, or attendee is the owner (spec #140). Nothing else counts: mail the owner only
received or was copied on doesn't, and neither does an event that lists no one. Mail forged
from one of the owner's addresses was never the owner's, so it doesn't either.

One of the owner's own episodes doesn't count: the exchange an answered proposal cites as
the owner's answer (`answers`). Answering "yes, close it" to a proposal about an unengaged
page is the owner's word on the proposal, not the owner's hand in the page. If it counted, the
answer would engage the page it was asked about, and dreaming could never retire it.

Engagement is kept by day, as `linked_days` keeps links, so it answers both "is this slug
engaged?" (it has any day) and "has it been engaged since a date?" (it has a day on or after
it).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from pa_core.librarian.metadata import L0Metadata
from pa_core.librarian.wiki import OWNER, day_logs, parse_observation, read_proposals
from pa_core.owner import Owner


def answers(tree: Path) -> frozenset[str]:
    """The episodes the answered proposals of the wiki at `tree` cite as the owner's answer:
    each one's last citation, which lint holds to one of the owner's exchanges."""
    return frozenset(
        proposal.citations[-1] for proposal in read_proposals(tree) if not proposal.is_open
    )


def engaged_days(tree: Path, l0: L0Metadata, owner: Owner) -> dict[str, list[date]]:
    """For each slug `owner` has engaged with, the distinct days of the observations that show
    it, in order, over every day log in the wiki at `tree`: those linking the slug and citing
    an episode the owner sent, wrote, organized, or attends, by `l0`, other than the owner's
    answer to a proposal (`answers`). A slug the owner hasn't engaged with isn't in it, and
    neither is the owner, `me`."""
    owners = l0.owner_episodes(owner) - answers(tree)
    days: dict[str, set[date]] = defaultdict(set)
    for day, lines in day_logs(tree).items():
        for line in lines:
            observation = parse_observation(line)
            if observation is None or owners.isdisjoint(observation.citations):
                continue
            for slug in observation.links:
                if slug != OWNER:
                    days[slug].add(day)
    return {slug: sorted(found) for slug, found in days.items()}
