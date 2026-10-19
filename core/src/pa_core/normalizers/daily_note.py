"""Daily note normalizer: one captured Obsidian daily note (`.md`) to an envelope."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import PurePosixPath

from pa_core.envelope import Envelope, Kind, Participant, Role, Source, TimeSpan, episode_id
from pa_core.errors import MalformedPayloadError
from pa_core.l0 import raw_ref
from pa_core.owner import Owner

EXTENSION = ".md"


def normalize_daily_note(raw: bytes, *, captured_at: datetime, owner: Owner) -> Envelope:
    """The note's envelope. Its `captured_at` is the capture header's `Captured-At`, not the
    `captured_at` given, because ingest may land a day's versions out of order (ADR-0002)."""
    path, captured_at, note = _split_capture(raw)
    start = datetime.combine(_day(path), time(), tzinfo=UTC)
    # Each distinct text of a note is its own version, so a correction is a new episode.
    eid = episode_id(Source.OBSIDIAN, path, hashlib.sha256(note).hexdigest())
    return Envelope.seal(
        episode_id=eid,
        source=Source.OBSIDIAN,
        kind=Kind.NOTE,
        native_id=path,
        occurred_at=TimeSpan(start=start, end=start + timedelta(days=1)),
        captured_at=captured_at,
        participants=[Participant(identifier=owner.identifier, name=owner.name, role=Role.AUTHOR)],
        thread_ref=path,
        body=_text(note).replace("\r\n", "\n").strip(),
        raw_ref=raw_ref(Source.OBSIDIAN, start, eid, EXTENSION),
    )


# A captured note is the note file's bytes behind a capture header: `Name: value` lines, then
# a blank line. The header records what the file alone does not say: its vault path, and when
# it was captured, which orders a day's versions.
_HEADER_END = re.compile(rb"\r?\n\r?\n")
_HEADER_LINE = re.compile(r"([A-Za-z0-9-]+):(.*)")


def _split_capture(raw: bytes) -> tuple[str, datetime, bytes]:
    """The note's vault path and capture time from its capture header, and the note's own
    bytes after it."""
    end = _HEADER_END.search(raw)
    if end is None:
        raise MalformedPayloadError("daily note has no capture header")
    fields: dict[str, str] = {}
    for line in _text(raw[: end.start()]).splitlines():
        field = _HEADER_LINE.fullmatch(line)
        if field is None:
            raise MalformedPayloadError("daily note has no capture header")
        fields[field[1].lower()] = field[2].strip()
    path = fields.get("vault-path")
    if not path:
        raise MalformedPayloadError("daily note has no Vault-Path in its capture header")
    return path, _captured_at(fields.get("captured-at")), raw[end.end() :]


def _captured_at(value: str | None) -> datetime:
    if not value:
        raise MalformedPayloadError("daily note has no Captured-At in its capture header")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None or moment.utcoffset() is None:
        raise MalformedPayloadError(
            f"daily note's Captured-At is not a time with a UTC offset ({value!r})"
        )
    return moment.astimezone(UTC)


# Daily notes are named for their day, which may sit among other words (`2026-10-05 Monday`).
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day(path: str) -> date:
    found = _ISO_DATE.search(PurePosixPath(path).stem)
    try:
        if found is None:
            raise ValueError
        return date.fromisoformat(found[0])
    except ValueError:
        raise MalformedPayloadError(f"daily note has no date in its name ({path!r})") from None


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPayloadError(f"daily note is not UTF-8 ({error})") from None
