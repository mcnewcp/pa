"""The owner: the one person a PA instance serves."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Owner:
    """Who the owner is. Instance configuration, never written into code.

    Email addresses are kept in lower case, as normalizers record participant identifiers.
    """

    name: str
    email_addresses: tuple[str, ...]
    other_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.email_addresses:
            raise ValueError("an owner needs at least one email address")
        lowered = tuple(address.lower() for address in self.email_addresses)
        object.__setattr__(self, "email_addresses", lowered)

    @cached_property
    def addresses(self) -> frozenset[str]:
        """The owner's email addresses as a set, in lowercase: what a participant's address
        is looked up in."""
        return frozenset(address.strip().lower() for address in self.email_addresses)

    @property
    def identifier(self) -> str:
        """How the owner appears as a participant: their first email address."""
        return self.email_addresses[0]
