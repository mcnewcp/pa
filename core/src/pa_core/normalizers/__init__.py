"""Normalizers: pure functions from a raw payload to an envelope, one per source shape."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import PurePath
from typing import Protocol

from pa_core.envelope import Envelope
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers import calendar, daily_note, email
from pa_core.owner import Owner


class Normalizer(Protocol):
    def __call__(self, raw: bytes, *, captured_at: datetime) -> Envelope: ...


# Raw payloads keep their native extension, which says which normalizer reads them.
def normalizer_for(name: str, owner: Owner) -> Normalizer:
    extension = PurePath(name).suffix.lower()
    match extension:
        case email.EXTENSION:
            return email.normalize_email
        case calendar.EXTENSION:
            return calendar.normalize_calendar_event
        case daily_note.EXTENSION:
            return partial(daily_note.normalize_daily_note, owner=owner)
        case _:
            raise MalformedPayloadError(f"{name}: no normalizer for '{extension}' files")
