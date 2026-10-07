"""The owner: the one person a PA instance serves."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    """Who the owner is. Instance configuration, never written into code."""

    name: str
    email_addresses: tuple[str, ...]
    other_names: tuple[str, ...] = ()

    @property
    def identifier(self) -> str:
        """How the owner appears as a participant: their first email address."""
        return self.email_addresses[0]
