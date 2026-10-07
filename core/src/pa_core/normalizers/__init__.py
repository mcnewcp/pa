"""Normalizers: pure functions from a raw payload to an envelope, one per source shape."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Protocol

from pa_core.envelope import Envelope
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers import calendar, email


class Normalizer(Protocol):
    def __call__(self, raw: bytes, *, captured_at: datetime) -> Envelope: ...


# Raw payloads keep their native extension, which says which normalizer reads them.
_BY_EXTENSION: dict[str, Normalizer] = {
    email.EXTENSION: email.normalize_email,
    calendar.EXTENSION: calendar.normalize_calendar_event,
}


def normalizer_for(name: str) -> Normalizer:
    extension = PurePath(name).suffix.lower()
    normalizer = _BY_EXTENSION.get(extension)
    if normalizer is None:
        raise MalformedPayloadError(f"{name}: no normalizer for '{extension}' files")
    return normalizer
