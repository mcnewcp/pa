"""Email normalizer: an RFC 5322 message (`.eml`) from Gmail to an envelope."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser

from pa_core.envelope import (
    Envelope,
    Kind,
    Participant,
    Role,
    Source,
    TimeSpan,
    episode_id,
    raw_ref,
)
from pa_core.errors import MalformedPayloadError

EXTENSION = ".eml"

_MESSAGE_ID = re.compile(r"<([^<>]+)>")
_ADDRESS_ROLES = (("From", Role.SENDER), ("To", Role.RECIPIENT), ("Cc", Role.CC))


def normalize_email(raw: bytes, *, captured_at: datetime) -> Envelope:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(message, EmailMessage)
    native_id = _message_id(message["Message-ID"])
    occurred_at = _date(message["Date"])
    eid = episode_id(Source.GMAIL, native_id)
    return Envelope.seal(
        episode_id=eid,
        source=Source.GMAIL,
        kind=Kind.EMAIL,
        native_id=native_id,
        occurred_at=TimeSpan(start=occurred_at),
        captured_at=captured_at,
        participants=_participants(message),
        thread_ref=_thread_ref(message, native_id),
        subject=_text(message["Subject"]),
        body=_body(message),
        labels=_labels(message["X-Gmail-Labels"]),
        raw_ref=raw_ref(Source.GMAIL, occurred_at, eid, EXTENSION),
    )


def _participants(message: EmailMessage) -> list[Participant]:
    participants: list[Participant] = []
    for header, role in _ADDRESS_ROLES:
        value = message[header]
        if value is None:
            continue
        address: Address
        for address in value.addresses:
            participants.append(
                Participant(
                    identifier=address.addr_spec.lower(),
                    name=address.display_name or None,
                    role=role,
                )
            )
    return participants


def _thread_ref(message: EmailMessage, native_id: str) -> str:
    """The thread's root message: first of References, else In-Reply-To, else this message."""
    for header in ("References", "In-Reply-To"):
        ids = _MESSAGE_ID.findall(str(message[header] or ""))
        if ids:
            return ids[0].strip()
    return native_id


def _body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    assert isinstance(part, EmailMessage)
    content: str = part.get_content()
    if part.get_content_subtype() == "html":
        content = _html_text(content)
    return content.replace("\r\n", "\n").strip()


def _html_text(html: str) -> str:
    extractor = _HtmlText()
    extractor.feed(html)
    extractor.close()
    lines = (" ".join(line.split()) for line in "".join(extractor.chunks).split("\n"))
    return "\n".join(line for line in lines if line)


class _HtmlText(HTMLParser):
    """Visible text of an HTML body, with a line break per block element."""

    _BLOCKS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})
    _HIDDEN = frozenset({"head", "script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._HIDDEN:
            self._hidden_depth += 1
        elif tag in self._BLOCKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._HIDDEN:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in self._BLOCKS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.chunks.append(data.replace("\n", " "))


def _labels(value: object) -> list[str]:
    if value is None:
        return []
    return [label.strip() for label in str(value).split(",") if label.strip()]


def _text(value: object) -> str | None:
    return None if value is None else str(value).strip()


def _message_id(value: object) -> str:
    message_id = "" if value is None else str(value).strip().strip("<>").strip()
    if not message_id:
        raise MalformedPayloadError("email has no Message-ID header")
    return message_id


def _date(value: object) -> datetime:
    moment = getattr(value, "datetime", None)
    if not isinstance(moment, datetime):
        raise MalformedPayloadError(f"email has no valid Date header (got {value!r})")
    # RFC 5322 reads a "-0000" offset as UTC with the sender's local zone unknown.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
