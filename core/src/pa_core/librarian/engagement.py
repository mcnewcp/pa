"""Engagement: which slugs the owner has had a hand in, from L0's metadata alone.

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
from typing import TYPE_CHECKING

from pa_core.envelope import Envelope, Role
from pa_core.librarian.identity import arrived_by_mail
from pa_core.librarian.wiki import OWNER, day_logs, parse_observation, read_proposals
from pa_core.owner import Owner

if TYPE_CHECKING:
    from pa_core.librarian.lint import L0Metadata

ENGAGING_ROLES = frozenset({Role.SENDER, Role.AUTHOR, Role.ORGANIZER, Role.ATTENDEE})
"""The parts in an episode that make it someone's own doing."""


def engagers(envelope: Envelope) -> frozenset[str]:
    """The addresses, in lowercase, that `envelope` is the doing of: its sender, author,
    organizers, and attendees.

    L0 is the owner's own mailbox, so the only sender it can vouch for is its own: mail that
    arrived from outside has no sender here, whatever its From line claims.
    """
    return frozenset(
        participant.identifier.strip().lower()
        for participant in envelope.participants
        if participant.role in ENGAGING_ROLES
        and not (participant.role is Role.SENDER and arrived_by_mail(envelope))
    )


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
    mine = {address.strip().lower() for address in owner.email_addresses}
    owners = {episode for episode, who in l0.engagers.items() if not who.isdisjoint(mine)}
    owners -= answers(tree)
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
