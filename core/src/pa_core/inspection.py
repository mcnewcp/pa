"""Structure inspection: what shape a payload, an envelope, or a quarantine record has, never
what it says.

A report holds only names, kinds, counts, and sizes: header and property names, content types,
component names, Gmail's system labels, envelope fields, and the quarantine reason. It never
holds a value that could identify a person or say what was written: no names, addresses,
subjects, bodies, summaries, locations, descriptions, URLs, user label names, or timestamps.

A name is printed only when it's one the format defines or an extension name (`X-…`): a payload
that isn't what it claims to be can make a parser read a line of text as a header, and the
first word of that line is content. Any other name is counted, never printed.

A payload that fails to parse still gets a report, of how far parsing got.
"""

from __future__ import annotations

import codecs
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import Message
from email.parser import BytesParser

from pa_core.envelope import Envelope, Kind, Role, Source
from pa_core.l0 import QuarantineRecord

FORMATS = ("email", "calendar", "note", "json")
"""The formats a payload can be read as; a file's suffix picks one (see `format_for`)."""

_SUFFIXES = {".eml": "email", ".ics": "calendar", ".md": "note", ".json": "json"}


def format_for(suffix: str) -> str | None:
    """The format a file with `suffix` is read as, or None for a suffix no payload has."""
    return _SUFFIXES.get(suffix.lower())


def inspect_structure(raw: bytes, fmt: str | None) -> str:
    """A report of the structure of `raw`, read as `fmt` (one of FORMATS, or None if unknown)."""
    report = _Report()
    report.add(f"format: {fmt or 'unknown; read it as one of ' + ', '.join(FORMATS)}")
    report.add(f"size: {len(raw)} bytes")
    match fmt:
        case "email":
            _email(raw, report)
        case "calendar":
            _calendar(raw, report)
        case "note":
            _note(raw, report)
        case "json":
            _json(raw, report)
        case _:
            pass
    return report.text()


class _Report:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, line: str, depth: int = 0) -> None:
        self._lines.append("  " * depth + line)

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"


def _names(names: Iterable[str], known: frozenset[str]) -> str:
    """`names` in order, each with its count if repeated, for those that may be printed (see the
    module's docstring), plus a count of the others."""
    counts = Counter(list(names))  # never a mapping, whose values would be read as counts
    shown: list[str] = []
    others = 0
    for name, count in counts.items():
        if name.upper() in known or name.upper().startswith("X-"):
            shown.append(name if count == 1 else f"{name} ({count})")
        else:
            others += count
    if others:
        shown.append(f"{others} other {_plural(others, 'name')}")
    return ", ".join(shown) or "none"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


# Email: an RFC 5322 message, its MIME parts, and Gmail's labels in Takeout's names.

_EMAIL_HEADERS = frozenset(
    name.upper()
    for name in [
        "Return-Path",
        "Received",
        "Date",
        "From",
        "Sender",
        "Reply-To",
        "To",
        "Cc",
        "Bcc",
        "Message-ID",
        "In-Reply-To",
        "References",
        "Subject",
        "Comments",
        "Keywords",
        "MIME-Version",
        "Content-Type",
        "Content-Transfer-Encoding",
        "Content-Disposition",
        "Content-ID",
        "Content-Description",
        "Content-Language",
        "Content-Location",
        "Resent-Date",
        "Resent-From",
        "Resent-Sender",
        "Resent-To",
        "Resent-Cc",
        "Resent-Bcc",
        "Resent-Message-ID",
        "DKIM-Signature",
        "ARC-Seal",
        "ARC-Message-Signature",
        "ARC-Authentication-Results",
        "Authentication-Results",
        "Received-SPF",
        "Delivered-To",
        "List-ID",
        "List-Unsubscribe",
        "List-Unsubscribe-Post",
        "List-Post",
        "List-Help",
        "List-Subscribe",
        "List-Archive",
        "List-Owner",
        "Precedence",
        "Auto-Submitted",
        "Importance",
        "Priority",
        "Thread-Topic",
        "Thread-Index",
        "Feedback-ID",
        "Organization",
        "User-Agent",
        "Disposition-Notification-To",
    ]
)
_ADDRESS_HEADERS = ("From", "Sender", "Reply-To", "To", "Cc", "Bcc")
_SYSTEM_LABELS = {
    label.upper(): label
    for label in (
        "Inbox",
        "Sent",
        "Important",
        "Starred",
        "Unread",
        "Opened",
        "Archived",
        "Drafts",
        "Spam",
        "Trash",
        "Chat",
        "Category Personal",
        "Category Promotions",
        "Category Social",
        "Category Updates",
        "Category Forums",
    )
}


def _email(raw: bytes, report: _Report) -> None:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as error:
        report.add(f"parse: stopped before the headers ({type(error).__name__})")
        return
    names = [name for name, _ in message.raw_items()]
    report.add(f"headers: {_names(names, _EMAIL_HEADERS)}")
    _gmail_labels(message, report)
    _address_counts(message, report)
    report.add("parts:")
    _mime_part(message, 1, report)
    defects = sorted({type(defect).__name__ for part in message.walk() for defect in part.defects})
    if defects:
        report.add(f"defects: {', '.join(defects)}")


def _gmail_labels(message: Message, report: _Report) -> None:
    values = [value for name, value in message.raw_items() if name.lower() == "x-gmail-labels"]
    if not values:
        return
    _labels(
        [label.strip() for value in values for label in str(value).split(",") if label.strip()],
        report,
    )


def _labels(labels: list[str], report: _Report) -> None:
    """Gmail's system labels by name, and a count of the owner's own labels."""
    system = [_SYSTEM_LABELS[label.upper()] for label in labels if label.upper() in _SYSTEM_LABELS]
    users = len(labels) - len(system)
    shown = ", ".join(system) or "no system labels"
    report.add(f"gmail labels: {shown}; {users} user {_plural(users, 'label')}")


def _address_counts(message: Message, report: _Report) -> None:
    counts: list[str] = []
    for header in _ADDRESS_HEADERS:
        if header not in message:
            continue
        try:
            value = message[header]
            count = len(getattr(value, "addresses", ()))
        except Exception as error:
            counts.append(f"{header} unreadable ({type(error).__name__})")
        else:
            counts.append(f"{header} {count}")
    if counts:
        report.add(f"addresses: {', '.join(counts)}")


def _mime_part(part: Message, depth: int, report: _Report) -> None:
    content_type = part.get_content_type()
    if part.is_multipart():
        children = part.get_payload()
        assert isinstance(children, list)
        report.add(f"{content_type}: {len(children)} {_plural(len(children), 'part')}", depth)
        for child in children:
            assert isinstance(child, Message)
            _mime_part(child, depth + 1, report)
        return
    details = [content_type]
    charset = part.get_content_charset()
    if charset is not None:
        details.append(_charset(charset))
    disposition = part.get_content_disposition()
    if disposition is not None:
        details.append(disposition if disposition in ("inline", "attachment") else "other")
    try:
        payload = part.get_payload(decode=True)
    except Exception as error:
        details.append(f"undecodable ({type(error).__name__})")
    else:
        size = len(payload) if isinstance(payload, bytes) else 0
        details.append(f"{size} bytes")
    report.add(", ".join(details), depth)


def _charset(name: str) -> str:
    try:
        return f"charset {codecs.lookup(name).name}"
    except LookupError:
        return "unknown charset"


# Capture headers: `Name: value` lines, then a blank line, ahead of what a collector captured
# (ADR-0002 for daily notes, ADR-0008 for calendar resources).

_CAPTURE_FIELDS = frozenset({"VAULT-PATH", "CAPTURED-AT", "CALENDAR", "DELETED-AT"})
_HEADER_END = re.compile(rb"\r?\n\r?\n")
_HEADER_LINE = re.compile(rb"([A-Za-z0-9-]+):")


def _capture_header(raw: bytes, report: _Report) -> bytes:
    """Report the capture header's field names, and return what follows the header.

    Anything that doesn't open with a `Name: value` line has no header, and all of it is
    returned; so has a calendar export, whose `BEGIN:VCALENDAR` reads like one. A header that
    stops making sense part way is reported as unreadable from there.
    """
    end = _HEADER_END.search(raw)
    lines = raw[: end.start()].splitlines() if end else []
    if not lines or _HEADER_LINE.match(lines[0]) is None or lines[0].upper().startswith(b"BEGIN:"):
        report.add("capture header: none")
        return raw
    names: list[str] = []
    for number, line in enumerate(lines, start=1):
        found = _HEADER_LINE.match(line)
        if found is None:
            report.add(f"capture header: {_names(names, _CAPTURE_FIELDS)}")
            report.add(f"capture header: line {number} is not a `Name: value` line")
            break
        names.append(found[1].decode("ascii"))
    else:
        report.add(f"capture header: {_names(names, _CAPTURE_FIELDS)}")
    return raw[end.end() :] if end else b""


# JSON: an envelope, a quarantine record, or an assistant chat exchange, told apart by their
# fields.

_ENVELOPE_FIELDS = frozenset(name.upper() for name in Envelope.model_fields)
_QUARANTINE_FIELDS = frozenset(name.upper() for name in QuarantineRecord.model_fields)
_EXCHANGE_FIELDS = frozenset({"SESSION_ID", "MESSAGE_ID", "STARTED_AT", "ENDED_AT", "TURNS"})


def _json(raw: bytes, report: _Report) -> None:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        report.add(f"parse: stopped at line {error.lineno} column {error.colno} ({error.msg})")
        return
    except UnicodeDecodeError as error:
        report.add(f"parse: stopped, not UTF-8 (first bad byte at offset {error.start})")
        return
    if not isinstance(document, dict):
        report.add(f"shape: a JSON {type(document).__name__}, not an object")
        return
    fields: dict[str, object] = document
    keys = set(fields)
    if "reason" in keys and "inbox_name" in keys:
        _quarantine_record(fields, report)
    elif "episode_id" in keys or "envelope_version" in keys:
        _envelope(fields, report)
    elif "turns" in keys or "session_id" in keys:
        _exchange(fields, report)
    else:
        report.add(f"shape: unrecognized, with {len(keys)} {_plural(len(keys), 'field')}")


def _envelope(fields: dict[str, object], report: _Report) -> None:
    report.add("shape: envelope")
    report.add(f"fields: {_names(fields, _ENVELOPE_FIELDS)}")
    missing = [name for name in Envelope.model_fields if name not in fields]
    if missing:
        report.add(f"missing: {', '.join(missing)}")
    for name, values in (("source", Source), ("kind", Kind)):
        value = fields.get(name)
        if value in set(values):
            report.add(f"{name}: {value}")
        elif name in fields:
            report.add(f"{name}: not one of the known values")
    participants = fields.get("participants")
    if isinstance(participants, list):
        roles = Counter(
            str(participant.get("role"))
            if isinstance(participant, dict) and participant.get("role") in set(Role)
            else "other"
            for participant in participants
        )
        by_role = ", ".join(f"{role} {count}" for role, count in roles.items())
        report.add(f"participants: {len(participants)}" + (f" ({by_role})" if by_role else ""))
    labels = fields.get("labels")
    if isinstance(labels, list):
        _labels([str(label) for label in labels], report)


def _quarantine_record(fields: dict[str, object], report: _Report) -> None:
    report.add("shape: quarantine record")
    report.add(f"fields: {_names(fields, _QUARANTINE_FIELDS)}")
    directory = str(fields.get("inbox_name", "")).partition("/")[0]
    if directory in set(Source):
        report.add(f"inbox directory: {directory}/")
    report.add(f"reason: {fields.get('reason')}")


def _exchange(fields: dict[str, object], report: _Report) -> None:
    report.add("shape: assistant chat exchange")
    report.add(f"fields: {_names(fields, _EXCHANGE_FIELDS)}")
    turns = fields.get("turns")
    if not isinstance(turns, list):
        return
    roles = Counter(
        str(turn.get("role"))
        if isinstance(turn, dict) and turn.get("role") in ("owner", "assistant")
        else "other"
        for turn in turns
    )
    by_role = ", ".join(f"{role} {count}" for role, count in roles.items())
    report.add(f"turns: {len(turns)}" + (f" ({by_role})" if by_role else ""))


# Daily note: an Obsidian note's Markdown behind a capture header (ADR-0002).

_HEADING = re.compile(rb"#{1,6}(?:[ \t]|$)")
_FENCE = re.compile(rb"(?:```|~~~)")


def _note(raw: bytes, report: _Report) -> None:
    note = _capture_header(raw, report)
    lines = note.splitlines()
    headings, fenced = 0, False
    for line in lines:
        if _FENCE.match(line.lstrip()):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            headings += 1
    report.add(f"note: {len(note)} bytes")
    report.add(f"lines: {len(lines)}")
    report.add(f"headings: {headings}")
    if note.startswith(b"---"):
        report.add("frontmatter: yes")
    try:
        note.decode("utf-8")
    except UnicodeDecodeError as error:
        report.add(f"encoding: not UTF-8 (first bad byte at offset {error.start})")


# Calendar: an iCalendar resource (RFC 5545), lines of `NAME;PARAM=value:VALUE` folded at 75
# octets and grouped into components by BEGIN and END lines.

_CALENDAR_COMPONENTS = frozenset(
    [
        "VCALENDAR",
        "VEVENT",
        "VTODO",
        "VJOURNAL",
        "VFREEBUSY",
        "VTIMEZONE",
        "STANDARD",
        "DAYLIGHT",
        "VALARM",
        "VLOCATION",
        "VRESOURCE",
        "VAVAILABILITY",
        "AVAILABLE",
        "PARTICIPANT",
    ]
)
_CALENDAR_PROPERTIES = frozenset(
    [
        "CALSCALE",
        "METHOD",
        "PRODID",
        "VERSION",
        "ATTACH",
        "CATEGORIES",
        "CLASS",
        "COMMENT",
        "DESCRIPTION",
        "GEO",
        "LOCATION",
        "PERCENT-COMPLETE",
        "PRIORITY",
        "RESOURCES",
        "STATUS",
        "SUMMARY",
        "COMPLETED",
        "DTEND",
        "DUE",
        "DTSTART",
        "DURATION",
        "FREEBUSY",
        "TRANSP",
        "TZID",
        "TZNAME",
        "TZOFFSETFROM",
        "TZOFFSETTO",
        "TZURL",
        "TZUNTIL",
        "ATTENDEE",
        "CONTACT",
        "ORGANIZER",
        "RECURRENCE-ID",
        "RELATED-TO",
        "URL",
        "UID",
        "EXDATE",
        "RDATE",
        "RRULE",
        "ACTION",
        "REPEAT",
        "TRIGGER",
        "CREATED",
        "DTSTAMP",
        "LAST-MODIFIED",
        "SEQUENCE",
        "REQUEST-STATUS",
        "NAME",
        "REFRESH-INTERVAL",
        "SOURCE",
        "COLOR",
        "IMAGE",
        "CONFERENCE",
        "ACKNOWLEDGED",
        "PROXIMITY",
        "STRUCTURED-DATA",
        "STYLED-DESCRIPTION",
    ]
)
_SERIES_PROPERTIES = ("RECURRENCE-ID", "RRULE", "EXDATE", "SEQUENCE", "LAST-MODIFIED")
"""What shows a resource's recurrence and version (ADR-0008); each event's are counted."""
_CONTENT_LINE = re.compile(r"([A-Za-z0-9-]+)[;:]")


@dataclass
class _Component:
    name: str
    properties: list[str] = field(default_factory=list)
    children: list[_Component] = field(default_factory=list)


def _calendar(raw: bytes, report: _Report) -> None:
    body = _capture_header(raw, report)
    unfolded = re.sub(rb"\r?\n[ \t]", b"", body)
    try:
        text = unfolded.decode("utf-8")
    except UnicodeDecodeError as error:
        report.add(f"encoding: not UTF-8 (first bad byte at offset {error.start})")
        text = unfolded.decode("utf-8", errors="replace")
    roots, problem = _components(text)
    report.add("components:")
    for root in roots:
        _component(root, 1, report)
    events = [child for root in roots for child in _walk(root) if child.name == "VEVENT"]
    report.add(f"events: {len(events)}")
    for name in _SERIES_PROPERTIES if events else ():
        having = sum(1 for event in events if name in event.properties)
        report.add(f"{name}: {having} of {len(events)}", 1)
    report.add(f"parse: {problem or 'complete'}")


def _components(text: str) -> tuple[list[_Component], str | None]:
    """The components read from `text`, and where reading stopped if it didn't finish."""
    roots: list[_Component] = []
    stack: list[_Component] = []
    lines = [line for line in text.splitlines() if line.strip()]
    for number, line in enumerate(lines, start=1):
        where = f"stopped at content line {number} of {len(lines)}"
        match = _CONTENT_LINE.match(line)
        if match is None:
            return roots, f"{where}: not a `NAME:value` line"
        name = match[1].upper()
        if name in ("BEGIN", "END"):
            component = line.partition(":")[2].strip().upper()
            if component not in _CALENDAR_COMPONENTS and not component.startswith("X-"):
                component = "(other component)"
            if name == "BEGIN":
                opened = _Component(component)
                (stack[-1].children if stack else roots).append(opened)
                stack.append(opened)
            elif stack and stack[-1].name == component:
                stack.pop()
            else:
                return roots, f"{where}: an END that matches no open component"
        elif not stack:
            return roots, f"{where}: a property outside any component"
        else:
            stack[-1].properties.append(name)
    if stack:
        return roots, f"ended inside {', '.join(still.name for still in stack)}"
    return roots, None


def _component(component: _Component, depth: int, report: _Report) -> None:
    names = _names(component.properties, _CALENDAR_PROPERTIES)
    report.add(f"{component.name}: {names}", depth)
    for child in component.children:
        _component(child, depth + 1, report)


def _walk(component: _Component) -> Iterator[_Component]:
    yield component
    for child in component.children:
        yield from _walk(child)
