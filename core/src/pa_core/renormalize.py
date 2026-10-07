"""Renormalize: rebuilding every envelope in L0 from its raw payload (ADR-0001)."""

from __future__ import annotations

from pa_core.envelope import Envelope
from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.normalizers import normalizer_for
from pa_core.owner import Owner


class RenormalizeError(PaError):
    """Renormalize stopped before the swap; the envelopes in L0 are as they were."""


def renormalize(store: L0Store, owner: Owner) -> int:
    """Rebuild every envelope in L0 from its raw payload with the current normalizers.

    The whole new envelope tree is built before anything changes, and then swapped in at
    once. Raw payloads are never touched. If any raw payload no longer normalizes to its
    episode's id, or no longer normalizes at all, nothing is swapped. Returns how many
    episodes were renormalized. The catalog is left for the caller to rebuild.
    """
    rebuilt: list[Envelope] = []
    problems: list[str] = []
    for old in store.envelopes():
        try:
            rebuilt.append(_rebuild(store, owner, old))
        except Exception as error:
            problems.append(f"{old.episode_id}: {error}")
    if problems:
        raise RenormalizeError(
            f"renormalize aborted, envelopes left unchanged ({len(problems)} problems):\n  "
            + "\n  ".join(problems)
        )
    store.replace_envelopes(rebuilt)
    return len(rebuilt)


def _rebuild(store: L0Store, owner: Owner, old: Envelope) -> Envelope:
    raw = store.get_raw(old.raw_ref)
    if raw is None:
        raise RenormalizeError(f"raw payload {old.raw_ref} is missing")
    # The capture time is not in the raw payload, so it carries over from the old envelope.
    new = normalizer_for(old.raw_ref, owner)(raw, captured_at=old.captured_at)
    if new.episode_id != old.episode_id:
        raise RenormalizeError(f"raw payload {old.raw_ref} now normalizes to {new.episode_id}")
    # Raw payloads never move, so the envelope keeps pointing at where its raw payload is,
    # even if a new `occurred_at` would partition it under another month today.
    return new.model_copy(update={"raw_ref": old.raw_ref})
