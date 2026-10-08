"""Assistant chat normalizer: one captured exchange with the assistant (`.json`) to an envelope.

The payload comes from the least trusted writer of the inbox (ADR-0004), so it must match its
schema exactly: no unknown fields, no missing ones, nothing but `owner` and `assistant` turns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails

from pa_core.envelope import Envelope, Kind, Participant, Role, Source, TimeSpan, episode_id
from pa_core.errors import MalformedPayloadError
from pa_core.l0 import raw_ref
from pa_core.owner import Owner

EXTENSION = ".json"

_Text = Annotated[str, StringConstraints(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class _Turn(_Strict):
    """One speaker's words: the owner's, or the assistant's."""

    role: Literal["owner", "assistant"]
    text: _Text


class _Exchange(_Strict):
    """The raw payload: one owner message in a session and the assistant's reply to it."""

    session_id: _Text
    message_id: _Text
    """The transcript id of the owner's message that opened the exchange."""
    started_at: AwareDatetime
    """When the owner's message was sent."""
    ended_at: AwareDatetime
    """When the assistant's reply ended."""
    turns: Annotated[list[_Turn], Field(min_length=1)]

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("the exchange ends before it starts")
        return self


def normalize_assistant_chat(raw: bytes, *, captured_at: datetime, owner: Owner) -> Envelope:
    exchange = _parse(raw)
    # The owner's message is unique within its session, so the two make the exchange's id.
    eid = episode_id(Source.ASSISTANT_CHAT, exchange.session_id, exchange.message_id)
    return Envelope.seal(
        episode_id=eid,
        source=Source.ASSISTANT_CHAT,
        kind=Kind.CHAT,
        native_id=exchange.message_id,
        occurred_at=TimeSpan(start=exchange.started_at, end=exchange.ended_at),
        captured_at=captured_at,
        # The assistant is not a person, so the owner is the exchange's only participant.
        participants=[Participant(identifier=owner.identifier, name=owner.name, role=Role.AUTHOR)],
        thread_ref=exchange.session_id,
        body="\n\n".join(f"{turn.role}: {turn.text}" for turn in exchange.turns),
        raw_ref=raw_ref(Source.ASSISTANT_CHAT, exchange.started_at, eid, EXTENSION),
    )


def _parse(raw: bytes) -> _Exchange:
    try:
        return _Exchange.model_validate_json(raw)
    except ValidationError as error:
        problems = "; ".join(_problem(detail) for detail in error.errors(include_url=False))
        raise MalformedPayloadError(
            f"assistant_chat payload does not match its schema: {problems}"
        ) from None


def _problem(detail: ErrorDetails) -> str:
    where = ".".join(str(part) for part in detail["loc"])
    match detail["type"]:
        case "extra_forbidden":
            return f"unknown field '{where}'"
        case "missing":
            return f"missing field '{where}'"
        case _:
            return f"{where}: {detail['msg']}" if where else detail["msg"]
