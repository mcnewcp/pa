"""Episode selection: which episodes a layer derived from L0 reads, decided from L0 alone.

- Mail by its Gmail category (`category`). Promotions and Social are bulk mail to skip; Updates
  and Forums need a check before they are read; Primary, no category, or one this module doesn't
  know is read, so nothing personal is ever skipped by accident.
- Of a daily note's versions, only the latest by capture time (`latest_note_versions`,
  ADR-0002).
- Of a chat, only the owner's turns (`pa_core.normalizers.assistant_chat.owner_turns`).

A message's category is read from its labels by name (`Category Promotions`, as a Gmail Takeout
export spells it), so a collector that sees Gmail's API label ids only has to map them onto the
same names.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pa_core.envelope import Envelope, Kind

_LABEL_PREFIX = "category "


class Category(StrEnum):
    PRIMARY = "primary"
    PROMOTIONS = "promotions"
    SOCIAL = "social"
    UPDATES = "updates"
    FORUMS = "forums"


# Gmail Takeout labels Primary mail `Category Personal`.
_BY_NAME = {"personal": Category.PRIMARY, **{c.value: c for c in Category}}

SKIPPED = frozenset({Category.PROMOTIONS, Category.SOCIAL})
"""Bulk mail, skipped without reading it."""
CHECKED = frozenset({Category.UPDATES, Category.FORUMS})
"""Mail that may or may not bear on the owner's own dealings, so it needs a check."""


def category(envelope: Envelope) -> Category:
    """The message's Gmail category, by name; Primary when it has none or an unknown one."""
    for label in envelope.labels:
        name = " ".join(label.split()).lower()
        if name.startswith(_LABEL_PREFIX):
            found = _BY_NAME.get(name.removeprefix(_LABEL_PREFIX))
            if found is not None and found is not Category.PRIMARY:
                return found
    return Category.PRIMARY


def latest_note_versions(envelopes: Sequence[Envelope]) -> list[Envelope]:
    """`envelopes` without the daily note versions a later version of the same note supersedes.

    A note's versions share its vault path (`native_id`) and are ordered by when each was
    captured, which ADR-0002 has the capture header record, then by episode id.
    """
    latest: dict[str, Envelope] = {}
    for envelope in envelopes:
        if envelope.kind is not Kind.NOTE:
            continue
        known = latest.get(envelope.native_id)
        if known is None or _version_order(envelope) > _version_order(known):
            latest[envelope.native_id] = envelope
    return [e for e in envelopes if e.kind is not Kind.NOTE or latest[e.native_id] is e]


def _version_order(envelope: Envelope) -> tuple[datetime, str]:
    return (envelope.captured_at, envelope.episode_id)
