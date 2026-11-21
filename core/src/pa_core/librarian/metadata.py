"""L0's metadata: what the host reads from the episodes' envelopes, and never from a model.

`L0Metadata` is every episode's id, its participants' addresses, and whose doing it is. Lint
checks the wiki's citations and identifiers against it, and engagement (`engagement.py`) is
read from it: an episode is someone's own doing when they sent, wrote, organized, or attend
it (`engagers`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pa_core.envelope import Envelope, Kind, Role
from pa_core.librarian.identity import arrived_by_mail
from pa_core.owner import Owner

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


@dataclass(frozen=True)
class L0Metadata:
    """What the host checks the wiki's evidence and identifiers against: L0's episodes and
    the addresses of their participants, every episode included, even those never offered."""

    episode_ids: frozenset[str]
    addresses: frozenset[str]
    """Every participant's address, in lowercase."""
    engagers: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """For each episode that is someone's own doing, the addresses it's the doing of, in
    lowercase (`engagers`): what engagement is read from."""
    exchanges: frozenset[str] = frozenset()
    """The episodes that are chat exchanges with the assistant."""

    @classmethod
    def of(cls, envelopes: Iterable[Envelope]) -> L0Metadata:
        ids: set[str] = set()
        addresses: set[str] = set()
        doers: dict[str, frozenset[str]] = {}
        exchanges: set[str] = set()
        for envelope in envelopes:
            ids.add(envelope.episode_id)
            addresses.update(
                p.identifier.strip().lower() for p in envelope.participants if "@" in p.identifier
            )
            if who := engagers(envelope):
                doers[envelope.episode_id] = who
            if envelope.kind is Kind.CHAT:
                exchanges.add(envelope.episode_id)
        return cls(frozenset(ids), frozenset(addresses), doers, frozenset(exchanges))

    def owner_episodes(self, owner: Owner) -> frozenset[str]:
        """The episodes that are `owner`'s own doing: those the owner sent, wrote, organized,
        or attends."""
        return frozenset(
            episode for episode, who in self.engagers.items() if not who.isdisjoint(owner.addresses)
        )

    def owner_exchanges(self, owner: Owner) -> frozenset[str]:
        """The chat exchanges `owner` took part in: where the owner's own word is."""
        return self.exchanges & self.owner_episodes(owner)

    @property
    def domains(self) -> frozenset[str]:
        """The mail domain of every address, as seen (subdomains included)."""
        return frozenset(a.rpartition("@")[2] for a in self.addresses)
