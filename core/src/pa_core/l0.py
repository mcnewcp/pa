"""The L0 storage interface: the append-only store of episodes (ADR-0001)."""

from __future__ import annotations

from typing import Protocol

from pa_core.envelope import Envelope


class L0Store(Protocol):
    def get(self, episode_id: str) -> Envelope | None:
        """The stored envelope for `episode_id`, or None if no such episode exists."""
        ...

    def put(self, envelope: Envelope, raw: bytes) -> None:
        """Write a new episode. Never called for an episode id that already exists."""
        ...
