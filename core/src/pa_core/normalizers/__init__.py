"""Normalizers: pure functions from a raw payload to an envelope, one per source."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Protocol

from pa_core.envelope import Envelope, Source
from pa_core.normalizers import assistant_chat, calendar, daily_note, email
from pa_core.owner import Owner


class Normalizer(Protocol):
    def __call__(self, raw: bytes, *, captured_at: datetime) -> Envelope: ...


_FORMATS: dict[Source, tuple[str, Callable[[Owner], Normalizer]]] = {
    Source.GMAIL: (email.EXTENSION, lambda _: email.normalize_email),
    Source.ICLOUD_CALENDAR: (calendar.EXTENSION, lambda _: calendar.normalize_calendar_event),
    Source.OBSIDIAN: (
        daily_note.EXTENSION,
        lambda owner: partial(daily_note.normalize_daily_note, owner=owner),
    ),
    Source.ASSISTANT_CHAT: (
        assistant_chat.EXTENSION,
        lambda owner: partial(assistant_chat.normalize_assistant_chat, owner=owner),
    ),
}
"""Each source's one payload format: its native extension, and its normalizer for an owner."""


_ID_PINS_CONTENT: dict[Source, Callable[[bytes], bool]] = {
    Source.ICLOUD_CALENDAR: calendar.id_pins_content,
}


def id_pins_content(source: Source, raw: bytes) -> bool:
    """Whether the episode id `raw` normalizes to already pins everything its envelope says.

    Then a payload landed under an id L0 already holds is the same version, even if its
    envelope differs in what's derived from when it was captured, such as a calendar series'
    occurrence list (ADR-0008). Elsewhere a difference means the source changed an item without
    changing its version, and the payload is a conflict.
    """
    pins = _ID_PINS_CONTENT.get(source)
    return pins is not None and pins(raw)


def payload_extension(source: Source) -> str:
    """The native extension of the one payload format `source` produces."""
    extension, _ = _FORMATS[source]
    return extension


def normalizer_for(source: Source, owner: Owner) -> Normalizer:
    """The normalizer for `source`'s payloads. The source is never read from the payload."""
    _, normalizer = _FORMATS[source]
    return normalizer(owner)
