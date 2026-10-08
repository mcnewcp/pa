"""Renormalize: rebuilding every envelope in L0 from its raw payload (ADR-0001)."""

from __future__ import annotations

from pa_core.envelope import Envelope
from pa_core.errors import PaError
from pa_core.l0 import L0Store
from pa_core.normalizers import normalizer_for
from pa_core.owner import Owner


class RenormalizeError(PaError):
    """Renormalize stopped before the swap; the envelopes in L0 are as they were."""


# What a raw payload the normalizers can no longer read raises: an expected PaError
# (MalformedPayloadError, or a RenormalizeError from `_rebuild`), a ValueError (bad values,
# undecodable text, an envelope that does not validate), or a LookupError for an unknown text
# encoding. Anything else is a bug in the code and is raised as is.
_PAYLOAD_ERRORS = (PaError, ValueError, LookupError)


def renormalize(store: L0Store, owner: Owner) -> int:
    """Rebuild the envelope of every raw payload in L0 with the current normalizers.

    The whole new envelope tree is built from the raw payloads before anything changes, and
    then swapped in at once. Raw payloads are never touched. Nothing is swapped if any raw
    payload no longer normalizes to its episode's id, or no longer normalizes at all, or has no
    envelope, or if an envelope's raw payload is missing. Returns how many episodes were
    renormalized. The catalog is left for the caller to rebuild.
    """
    old_envelopes = {envelope.raw_ref: envelope for envelope in store.envelopes()}
    rebuilt: list[Envelope] = []
    problems: list[str] = []
    for raw_ref in store.raw_refs():
        old = old_envelopes.pop(raw_ref, None)
        if old is None:
            # Its capture time is only in an envelope, so it cannot be rebuilt here. Ingest
            # stopped between the two writes, and the payload is still in the inbox.
            problems.append(
                f"{raw_ref}: raw payload has no envelope (an ingest stopped part way); "
                "run `pa ingest` to finish it"
            )
            continue
        try:
            rebuilt.append(_rebuild(store, owner, old))
        except _PAYLOAD_ERRORS as error:
            if isinstance(error, KeyError | IndexError):
                raise  # lookup errors from code, not from reading a payload
            problems.append(f"{old.episode_id}: {error}")
    problems += [
        f"{old.episode_id}: raw payload {old.raw_ref} is missing" for old in old_envelopes.values()
    ]
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
    new = normalizer_for(old.source, owner)(raw, captured_at=old.captured_at)
    if new.episode_id != old.episode_id:
        raise RenormalizeError(f"raw payload {old.raw_ref} now normalizes to {new.episode_id}")
    # Raw payloads never move, so the envelope keeps pointing at where its raw payload is,
    # even if a new `occurred_at` would partition it under another month today.
    return new.model_copy(update={"raw_ref": old.raw_ref})
