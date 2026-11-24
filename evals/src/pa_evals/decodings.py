"""Every way a reproduction's text reads, beyond how it is written (ADR-0010).

A reproduction's payloads are released as they were left, byte for byte, and a development
session commits them. So the release check must have read whatever any reader of those bytes
can: a mail client shows a part ingest never opens, and a header's encoded word, a base64
part, a folded line, or a character reference each write a name so that no line of the file
holds it. What is released has been checked in every form its format gives it, or it isn't
released.

This module gives the check those forms:

- `decode_payload`: a raw payload's decodings by its source's format. A mail's every header
  and parameter, and every part by its transfer encoding and character set, an HTML part also
  as it shows; a calendar file's lines unfolded, and its text unescaped; a chat's strings as
  JSON reads them. And what in the payload can't be decoded to text at all (`Decoded.unread`:
  a part that isn't text, an encoding or a character set nobody knows), which refuses it.
- `text_decodings`: the encodings any text may hold, whatever file it is in: character
  references, percent-encoding, and backslash escapes.
- `embedded_data`: data a text carries encoded in a `data:` URI, which nothing here reads.
- `Said`: which decodings say nothing the text before them didn't, so one thing found is
  counted once.

What isn't a decoding stays out of reach, as ADR-0010 accepts: text respelled or reworded, or
encoded by hand in a way no format names.

Everything here is a session's own words, or a description that may quote them: private.
"""

from __future__ import annotations

import base64
import binascii
import html
import json
import quopri
import re
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from html.parser import HTMLParser

from pa_core.envelope import Source


@dataclass(frozen=True)
class Decoding:
    """One way a text reads that its written lines may not show."""

    what: str
    """Which decoding it is, for the check's private notes: "part 2 (text/html), decoded"."""
    text: str


@dataclass(frozen=True)
class Decoded:
    """A raw payload, decoded every way its format can be."""

    decodings: tuple[Decoding, ...] = ()
    unread: tuple[str, ...] = ()
    """Each thing in it that can't be decoded to text, and so can't be checked."""


def decode_payload(source: Source, raw: bytes) -> Decoded:
    """The decodings of the raw payload `raw`, which is UTF-8 text, by the format `source`
    produces. A payload its format can't parse has none: ingest quarantines it."""
    match source:
        case Source.GMAIL:
            return _mail(raw)
        case Source.ICLOUD_CALENDAR:
            return _calendar(raw.decode())
        case Source.ASSISTANT_CHAT:
            return _chat(raw.decode())
        case Source.OBSIDIAN:
            # A note is its own text, behind a capture header of plain lines.
            return Decoded()


# Any text.

_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_ESCAPE = re.compile(r"\\(?:u([0-9A-Fa-f]{4})|U([0-9A-Fa-f]{8})|x([0-9A-Fa-f]{2}))")
_DATA_URI = re.compile(r"\bdata:[^\s,]*;base64,", re.IGNORECASE)


def text_decodings(text: str) -> list[Decoding]:
    """`text` with each encoding any text may hold read, wherever that changes it: its
    character references (`&#79;`, `&amp;`), its percent-encoding (`%4F`), and its backslash
    escapes (`\\u004f`). Each is read alone, as a reader that knows one of them would."""
    read = [
        ("with its character references read", html.unescape(text) if "&" in text else text),
        (
            "with its percent-encoding read",
            urllib.parse.unquote(text) if _PERCENT.search(text) else text,
        ),
        ("with its backslash escapes read", _unescaped(text)),
    ]
    return [Decoding(what, decoded) for what, decoded in read if decoded != text]


def _unescaped(text: str) -> str:
    def character(escape: re.Match[str]) -> str:
        point = int(escape[1] or escape[2] or escape[3], 16)
        return chr(point) if point <= 0x10FFFF else escape[0]

    read = _ESCAPE.sub(character, text)
    if read == text:
        return text
    # Two escapes can be one character's surrogate pair, as JSON writes it.
    return read.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def embedded_data(text: str) -> int:
    """How many `data:` URIs in `text` carry their data in base64: bytes inside a text, which
    no rule reads."""
    return len(_DATA_URI.findall(text))


class Said:
    """What has been read of a file so far, line by line: its written lines, and each decoding
    kept. A decoding whose every line is already there says nothing new, and is left out, so
    what a rule finds in a file is found once."""

    def __init__(self, written: str) -> None:
        self._lines: set[str] = set()
        self._texts: list[str] = []
        self.add(written)

    def add(self, text: str) -> None:
        self._lines.update(text.splitlines())
        self._texts.append(text)

    def new(self, text: str) -> bool:
        """Whether `text` holds a line that nothing read so far does. If so it is kept."""
        if all(self._holds(line.strip()) for line in text.splitlines()):
            return False
        self.add(text)
        return True

    def _holds(self, line: str) -> bool:
        if not line or line in self._lines:
            return True
        # Inside a longer line, between characters that can't make a word, an address, a
        # domain, or a number of it something else than it is by itself.
        within = re.compile(rf"(?<![\w.@-]){re.escape(line)}(?![\w.@-])")
        return any(line in text and within.search(text) for text in self._texts)


# A mail (RFC 5322, MIME).

_IDENTITY = frozenset({"", "7bit", "8bit", "binary"})
"""The transfer encodings that leave a part as it is written."""
_TEXT_SUBTYPES = frozenset({"plain", "html", "calendar", "markdown", "csv"})
"""The kinds of text part that are read. Another kind may have encodings of its own."""
_PARAMETERIZED = ("Content-Type", "Content-Disposition")
_SAID_ONCE = ("Content-Type", "Content-Transfer-Encoding")
"""The headers that say how a part is read. Two of one would be read either way."""
_FOLD = re.compile(r"\r?\n(?=[ \t])")
_ENCODED_WORD = re.compile(r"=\?([^?]*)\?([bBqQ])\?(.*?)\?=")
_BETWEEN_ENCODED_WORDS = re.compile(r"(?<=\?=)[ \t]+(?==\?)")


def _mail(raw: bytes) -> Decoded:
    decodings: list[Decoding] = []
    unread: list[str] = []
    for number, part in enumerate(message_from_bytes(raw).walk()):
        whose = "its" if number == 0 else f"part {number}'s"
        for name, value in part.raw_items():
            try:
                read = _header(str(value).encode("ascii", "surrogateescape").decode())
            except (LookupError, ValueError):
                unread.append(f"{whose} {name} header holds an encoded word that can't be read")
                continue
            decodings += [Decoding(f"{whose} {name} header, decoded", text) for text in read]
        for header in _PARAMETERIZED:
            for key, value in part.get_params(failobj=[], header=header):
                try:
                    text = _parameter(value)
                except (LookupError, ValueError):
                    unread.append(f"{whose} {header} header's {key} can't be read")
                    continue
                if text:
                    decodings.append(Decoding(f"{whose} {header} header's {key}, decoded", text))
        body = _body(part, "its body" if number == 0 else f"part {number}")
        decodings += body.decodings
        unread += body.unread
    return Decoded(tuple(decodings), tuple(unread))


def _header(value: str) -> list[str]:
    """A header's value unfolded, with its encoded words (RFC 2047) read: once as the RFC has
    it, without the space between two encoded words, and once with it, as a reader that keeps
    it would. Raises LookupError for a character set nobody knows, and ValueError for bytes
    that aren't in the one named."""

    def word(encoded: re.Match[str]) -> str:
        charset, encoding, text = encoded.groups()
        if encoding in "bB":
            data = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
        else:
            data = quopri.decodestring(text.replace("_", " ").encode(), header=False)
        # A character set may carry a language after a `*` (RFC 2231).
        return data.decode(charset.partition("*")[0] or "us-ascii")

    unfolded = _FOLD.sub("", value)
    return [
        _ENCODED_WORD.sub(word, _BETWEEN_ENCODED_WORDS.sub("", unfolded)),
        _ENCODED_WORD.sub(word, unfolded),
    ]


def _parameter(value: str | tuple[str | None, str | None, str]) -> str:
    """A header's parameter, which may be written percent-encoded in a character set and in
    several pieces (RFC 2231)."""
    if isinstance(value, str):
        return value
    charset, _, encoded = value
    return bytes(encoded, "raw-unicode-escape").decode(charset or "us-ascii")


def _body(part: Message, name: str) -> Decoded:
    """What the part holds itself, by its transfer encoding and its character set."""
    kind = part.get_content_type()
    encoding = str(part.get("Content-Transfer-Encoding", "7bit")).strip().lower()
    described = f"{name} ({kind})"
    if any(len(part.get_all(header, [])) > 1 for header in _SAID_ONCE):
        return Decoded(unread=(f"{name} says twice what it is or how it is encoded",))
    if part.is_multipart():
        # A part that holds parts is read as it is written. A reader that decoded it first
        # would find others.
        if encoding in _IDENTITY:
            return Decoded()
        return Decoded(unread=(f"{described} holds other parts, and is encoded as {encoding}",))
    # Without the header, the payload comes back as the bytes it is written in.
    del part["Content-Transfer-Encoding"]
    data = part.get_payload(decode=True)
    if not isinstance(data, bytes):
        return Decoded(unread=(f"{described} has no text of its own",))
    if encoding == "base64":
        try:
            data = base64.b64decode(b"".join(data.split()), validate=True)
        except binascii.Error:
            return Decoded(unread=(f"{described} is encoded as base64, and isn't base64",))
    elif encoding == "quoted-printable":
        data = quopri.decodestring(data)
    elif encoding not in _IDENTITY:
        return Decoded(unread=(f"{described} is in an encoding that isn't read ({encoding})",))
    if part.get_content_maintype() != "text":
        if encoding in _IDENTITY and part.get_content_maintype() in ("multipart", "message"):
            return Decoded()  # Parts that didn't parse as parts: text, as it is written.
        return Decoded(unread=(f"{described} isn't text",))
    if part.get_content_subtype() not in _TEXT_SUBTYPES:
        return Decoded(unread=(f"{described} is a kind of text that isn't read",))
    try:
        text = data.decode(part.get_content_charset() or "utf-8")
    except (LookupError, ValueError):
        return Decoded(unread=(f"{described} isn't text in the character set it names",))
    decodings = [Decoding(f"{described}, decoded", text)]
    unread: tuple[str, ...] = ()
    match part.get_content_subtype():
        case "html":
            shown = _shown(text)
            decodings += [
                Decoding(f"{described}, as it shows", "".join(shown)),
                Decoding(f"{described}, as it shows, piece by piece", "\n".join(shown)),
            ]
        case "calendar":
            calendar = _calendar(text)
            unread = tuple(f"{described}: {what}" for what in calendar.unread)
            decodings += [
                Decoding(f"{described}, {decoding.what}", decoding.text)
                for decoding in calendar.decodings
            ]
        case "plain" if str(part.get_param("format", "")).lower() == "flowed":
            delete_space = str(part.get_param("delsp", "")).lower() == "yes"
            decodings.append(
                Decoding(f"{described}, with its lines flowed", _flowed(text, delete_space))
            )
    return Decoded(tuple(decodings), unread)


class _Shown(HTMLParser):
    """The text of an HTML document, each run between two tags, with its character references
    read. A hidden element's text is released with the rest, so it counts too."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[str] = []

    def handle_data(self, data: str) -> None:
        self.runs.append(data)


def _shown(document: str) -> list[str]:
    parser = _Shown()
    parser.feed(document)
    parser.close()
    return parser.runs


def _flowed(text: str, delete_space: bool) -> str:
    """Text written `format=flowed` (RFC 3676), as it reads: a line that ends in a space runs
    on into the next, and with `delsp=yes` that space isn't part of it."""
    flowed: list[str] = []
    runs_on = False
    for line in text.splitlines():
        line = line.removeprefix(" ")
        soft = line.endswith(" ") and line != "-- "
        piece = line[:-1] if soft and delete_space else line
        if runs_on:
            flowed[-1] += piece
        else:
            flowed.append(piece)
        runs_on = soft
    return "\n".join(flowed)


# A calendar file (RFC 5545).

_CALENDAR_FOLD = re.compile(r"\r?\n[ \t]")
_CALENDAR_ESCAPE = re.compile(r"\\([\\;,nN])|\^([n'^])")
_ENCODED_PROPERTY = re.compile(r';(ENCODING|VALUE)="?([^";:,]*)', re.IGNORECASE)


def _calendar(text: str) -> Decoded:
    """A calendar file's lines unfolded, and its text and parameter values unescaped (RFC 5545,
    RFC 6868). A property in an encoding of its own (`ENCODING=BASE64`, `VALUE=BINARY`) can't
    be read."""
    unfolded = _CALENDAR_FOLD.sub("", text)
    unread = [
        f"the property on line {number}, unfolded, is in an encoding of its own"
        for number, line in enumerate(unfolded.splitlines(), start=1)
        if any(
            value.upper() != "8BIT"
            if parameter.upper() == "ENCODING"
            else value.upper() == "BINARY"
            for parameter, value in _ENCODED_PROPERTY.findall(_before_its_value(line))
        )
    ]
    unescaped = _CALENDAR_ESCAPE.sub(_calendar_character, unfolded)
    return Decoded(
        (Decoding("its lines, unfolded", unfolded), Decoding("its text, unescaped", unescaped)),
        tuple(unread),
    )


def _calendar_character(escape: re.Match[str]) -> str:
    """What an escape stands for: a text value's (`\\n`, `\\,`), or a parameter's (`^n`,
    `^'`)."""
    character = escape[1] or escape[2]
    if character in "nN":
        return "\n"
    return '"' if escape[0] == "^'" else character


def _before_its_value(line: str) -> str:
    """A content line's name and parameters: what is before its first colon outside quotes."""
    quoted = False
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            return line[:index]
    return line


# A chat (JSON).


def _chat(text: str) -> Decoded:
    """Every string of a JSON payload, names and values, as JSON reads them: a string may
    write any character as an escape."""
    try:
        value = json.loads(text)
    except ValueError:
        return Decoded()
    return Decoded((Decoding("its strings, as JSON reads them", "\n".join(_strings(value))),))


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for name, item in value.items():
            yield str(name)
            yield from _strings(item)
