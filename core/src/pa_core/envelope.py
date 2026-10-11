"""The envelope: the normalized, source-independent description of an episode."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

ENVELOPE_VERSION = 1

# Fields that can change between captures of the same item without changing what it says.
# They are left out of `content_hash` so a re-capture collapses instead of conflicting.
VOLATILE_FIELDS = frozenset({"captured_at", "raw_ref", "envelope_version", "labels"})


class Source(StrEnum):
    GMAIL = "gmail"
    ICLOUD_CALENDAR = "icloud_calendar"
    OBSIDIAN = "obsidian"
    ASSISTANT_CHAT = "assistant_chat"


class Kind(StrEnum):
    EMAIL = "email"
    CALENDAR_EVENT = "calendar_event"
    NOTE = "note"
    CHAT = "chat"


class Role(StrEnum):
    SENDER = "sender"
    RECIPIENT = "recipient"
    CC = "cc"
    ATTENDEE = "attendee"
    ORGANIZER = "organizer"
    AUTHOR = "author"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Participant(_Frozen):
    identifier: str
    role: Role
    name: str | None = None


class TimeSpan(_Frozen):
    """When an episode happened; `end` is set only where the source has one."""

    start: AwareDatetime
    end: AwareDatetime | None = None


class Envelope(_Frozen):
    episode_id: str
    source: Source
    kind: Kind
    native_id: str
    occurred_at: TimeSpan
    captured_at: AwareDatetime
    participants: list[Participant]
    thread_ref: str | None = None
    calendar_event_ref: str | None = None
    calendar_name: str | None = None
    subject: str | None = None
    body: str
    labels: list[str] = []
    raw_ref: str
    content_hash: str
    envelope_version: Literal[1] = ENVELOPE_VERSION

    @classmethod
    def seal(cls, **fields: Any) -> Envelope:
        """Build an envelope and stamp its `content_hash`."""
        unsealed = cls.model_validate({**fields, "content_hash": ""})
        return unsealed.model_copy(update={"content_hash": content_hash(unsealed)})


def episode_id(source: Source, native_id: str, version: str = "") -> str:
    """`<source>_` plus the first 16 hex of SHA-256 over the native id and version component.

    Derived from the raw payload alone, so it survives renormalize.
    """
    key = native_id if not version else f"{native_id}\n{version}"
    return f"{source}_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def content_hash(envelope: Envelope) -> str:
    """SHA-256 over the envelope's meaningful content, excluding volatile fields."""
    content = envelope.model_dump(mode="json", exclude={*VOLATILE_FIELDS, "content_hash"})
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
