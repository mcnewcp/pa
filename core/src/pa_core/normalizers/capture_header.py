"""Capture headers: what a collector knows about a payload that the payload's own bytes don't
say, as `Name: value` lines and a blank line ahead of them (ADR-0002 for daily notes, ADR-0008
for calendar resources).

Every header has `Captured-At`, when the collector read the payload, which becomes the
envelope's `captured_at`; each source declares the other fields it requires. Names are matched
without regard to case, and fields a source doesn't know are ignored, so a collector can add one
without breaking older payloads. Error messages name the field, never its value (#82).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pa_core.errors import MalformedPayloadError

_HEADER_END = re.compile(rb"\r?\n\r?\n")
_HEADER_LINE = re.compile(r"([A-Za-z0-9-]+):(.*)")


@dataclass(frozen=True)
class CaptureHeader:
    """A payload's capture header. `what` names the payload in error messages."""

    what: str
    fields: Mapping[str, str]
    """Each field's value, by its name in lower case."""

    @property
    def captured_at(self) -> datetime:
        """When the collector read the payload, in UTC."""
        moment = self.time("Captured-At")
        if moment is None:
            raise MalformedPayloadError(f"{self.what} has no Captured-At in its capture header")
        return moment

    def get(self, name: str) -> str | None:
        """The field `name`, if present."""
        return self.fields.get(name.lower())

    def require(self, name: str) -> str:
        """The field `name`, which must be present and not empty."""
        value = self.get(name)
        if not value:
            raise MalformedPayloadError(f"{self.what} has no {name} in its capture header")
        return value

    def time(self, name: str) -> datetime | None:
        """The field `name` as a UTC time, if present. It must be an ISO 8601 time with a UTC
        offset."""
        value = self.get(name)
        if value is None:
            return None
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            moment = None
        if moment is None or moment.utcoffset() is None:
            raise MalformedPayloadError(f"{self.what}'s {name} is not a time with a UTC offset")
        return moment.astimezone(UTC)


def split_capture_header(
    raw: bytes, *, what: str, required: Sequence[str]
) -> tuple[CaptureHeader, bytes]:
    """The capture header at the start of `raw`, and the payload's own bytes after it.

    Raises MalformedPayloadError when there is no header, or when a `required` field or
    `Captured-At` is missing or empty.
    """
    end = _HEADER_END.search(raw)
    if end is None:
        raise MalformedPayloadError(f"{what} has no capture header")
    try:
        text = raw[: end.start()].decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPayloadError(f"{what} is not UTF-8 ({error})") from None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        field = _HEADER_LINE.fullmatch(line)
        if field is None:
            raise MalformedPayloadError(f"{what} has no capture header")
        fields[field[1].lower()] = field[2].strip()
    header = CaptureHeader(what, fields)
    for name in (*required, "Captured-At"):
        header.require(name)
    return header, raw[end.end() :]
