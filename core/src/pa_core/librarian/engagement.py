"""Engagement: which slugs the owner has had a hand in, from L0's metadata alone.

A slug is engaged when an observation linking it cites an episode whose sender, author,
organizer, or attendee is the owner (spec #140). Nothing else counts: mail the owner only
received or was copied on doesn't, and neither does an event that lists no one. Mail forged
from one of the owner's addresses was never the owner's, so it doesn't either.

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
from pa_core.librarian.wiki import OWNER, day_logs, parse_observation
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


def engaged_days(tree: Path, l0: L0Metadata, owner: Owner) -> dict[str, list[date]]:
    """For each slug `owner` has engaged with, the distinct days of the observations that show
    it, in order, over every day log in the wiki at `tree`: those linking the slug and citing
    an episode the owner sent, wrote, organized, or attends, by `l0`. A slug the owner hasn't
    engaged with isn't in it, and neither is the owner, `me`."""
    mine = {address.strip().lower() for address in owner.email_addresses}
    owners = {episode for episode, who in l0.engagers.items() if not who.isdisjoint(mine)}
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
