"""Normalizers: pure functions from a raw payload to an envelope, one per source."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Protocol

from pa_core.envelope import Envelope, Source
from pa_core.normalizers import assistant_chat, calendar, daily_note, email
from pa_core.owner import Owner


class Normalizer(Protocol):
    def __call__(self, raw: bytes, *, captured_at: datetime) -> Envelope: ...


def payload_extension(source: Source) -> str:
    """The native extension of the one payload format `source` produces."""
    match source:
        case Source.GMAIL:
            return email.EXTENSION
        case Source.ICLOUD_CALENDAR:
            return calendar.EXTENSION
        case Source.OBSIDIAN:
            return daily_note.EXTENSION
        case Source.ASSISTANT_CHAT:
            return assistant_chat.EXTENSION


def normalizer_for(source: Source, owner: Owner) -> Normalizer:
    """The normalizer for `source`'s payloads. The source is never read from the payload."""
    match source:
        case Source.GMAIL:
            return email.normalize_email
        case Source.ICLOUD_CALENDAR:
            return calendar.normalize_calendar_event
        case Source.OBSIDIAN:
            return partial(daily_note.normalize_daily_note, owner=owner)
        case Source.ASSISTANT_CHAT:
            return partial(assistant_chat.normalize_assistant_chat, owner=owner)
