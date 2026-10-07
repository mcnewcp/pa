"""Calendar normalizer: one iCalendar event (`.ics`) from iCloud Calendar to an envelope."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_core.envelope import Envelope, Kind, Participant, Role, Source, TimeSpan, episode_id
from pa_core.errors import MalformedPayloadError
from pa_core.l0 import raw_ref

EXTENSION = ".ics"


def normalize_calendar_event(raw: bytes, *, captured_at: datetime) -> Envelope:
    calendar = _parse(raw)
    event = _the_event(calendar)
    native_id = event.text("UID")
    if not native_id:
        raise MalformedPayloadError("calendar event has no UID")
    start = _moment(event, "DTSTART", calendar)
    if start is None:
        raise MalformedPayloadError("calendar event has no DTSTART")
    end = _moment(event, "DTEND", calendar)
    eid = episode_id(Source.ICLOUD_CALENDAR, native_id, _version(event))
    return Envelope.seal(
        episode_id=eid,
        source=Source.ICLOUD_CALENDAR,
        kind=Kind.CALENDAR_EVENT,
        native_id=native_id,
        occurred_at=TimeSpan(start=start, end=end),
        captured_at=captured_at,
        participants=_participants(event),
        calendar_name=calendar.text("X-WR-CALNAME"),
        subject=event.text("SUMMARY"),
        thread_ref=native_id,
        body=_body(event),
        raw_ref=raw_ref(Source.ICLOUD_CALENDAR, start, eid, EXTENSION),
    )


_PARTICIPANT_ROLES = (("ORGANIZER", Role.ORGANIZER), ("ATTENDEE", Role.ATTENDEE))


def _participants(event: _Component) -> list[Participant]:
    return [
        Participant(identifier=_address(prop.value), name=prop.params.get("CN") or None, role=role)
        for name, role in _PARTICIPANT_ROLES
        for prop in event.properties.get(name, [])
    ]


def _address(value: str) -> str:
    """An email address from a `mailto:` URI; any other calendar user address as given."""
    if value[:7].lower() == "mailto:":
        return value[7:].strip().lower()
    return value.strip()


def _body(event: _Component) -> str:
    """The event's status and location as labelled lines, then its description."""
    status = event.text("STATUS")
    fields = [("Status", status.lower() if status else None), ("Location", event.text("LOCATION"))]
    header = "\n".join(f"{label}: {value}" for label, value in fields if value)
    description = (event.text("DESCRIPTION") or "").replace("\r\n", "\n")
    return "\n\n".join(part for part in (header, description) if part)


def _version(event: _Component) -> str:
    """Which version of the event this is: its sequence number and last-modified time.

    DTSTAMP is left out because it changes with every export of the same version.
    """
    return f"{event.text('SEQUENCE') or '0'}:{event.text('LAST-MODIFIED') or ''}"


def _the_event(calendar: _Component) -> _Component:
    events = [child for child in calendar.children if child.name == "VEVENT"]
    if len(events) != 1:
        raise MalformedPayloadError(f"calendar has {len(events)} events; expected exactly one")
    return events[0]


# A DATE-TIME (`20261017T090000`, `Z` for UTC) or, for all-day events, a DATE (`20261017`).
_DATE_TIME = re.compile(r"(\d{8})(?:T(\d{6}))?(Z?)")


def _moment(event: _Component, name: str, calendar: _Component) -> datetime | None:
    """A DATE or DATE-TIME property as an aware datetime, if present.

    A time keeps the zone the source gave it. A date, or a time with no zone, is read in the
    calendar's own time zone, else UTC.
    """
    prop = event.first(name)
    if prop is None:
        return None
    match = _DATE_TIME.fullmatch(prop.value.strip())
    if match is None or (match[3] and not match[2]):
        raise MalformedPayloadError(f"calendar event has an unreadable {name} ({prop.value!r})")
    try:
        naive = datetime.strptime(match[1] + (match[2] or "000000"), "%Y%m%d%H%M%S")
    except ValueError:
        raise MalformedPayloadError(
            f"calendar event has an unreadable {name} ({prop.value!r})"
        ) from None
    if match[3]:
        return naive.replace(tzinfo=UTC)
    return naive.replace(tzinfo=_zone(prop.params.get("TZID") or calendar.text("X-WR-TIMEZONE")))


def _zone(name: str | None) -> tzinfo:
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise MalformedPayloadError(f"calendar names an unknown time zone ({name!r})") from None


# iCalendar (RFC 5545) is lines of `NAME;PARAM=value:VALUE`, folded at 75 octets, grouped into
# components by BEGIN and END lines.


@dataclass
class _Property:
    value: str
    params: dict[str, str]


@dataclass
class _Component:
    name: str
    properties: dict[str, list[_Property]] = field(default_factory=dict)
    children: list[_Component] = field(default_factory=list)

    def first(self, name: str) -> _Property | None:
        found = self.properties.get(name)
        return found[0] if found else None

    def text(self, name: str) -> str | None:
        prop = self.first(name)
        return None if prop is None else _unescape(prop.value).strip()


def _parse(raw: bytes) -> _Component:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPayloadError(f"calendar is not UTF-8 ({error})") from None
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    stack: list[_Component] = []
    root: _Component | None = None
    for line in unfolded.splitlines():
        if not line.strip():
            continue
        name, params, value = _content_line(line)
        if name == "BEGIN":
            component = _Component(value.upper())
            if stack:
                stack[-1].children.append(component)
            elif root is None:
                root = component
            else:
                raise MalformedPayloadError("calendar has more than one top-level component")
            stack.append(component)
        elif name == "END":
            if not stack or stack[-1].name != value.upper():
                raise MalformedPayloadError(f"calendar has an unmatched END:{value}")
            stack.pop()
        elif stack:
            stack[-1].properties.setdefault(name, []).append(_Property(value, params))
        else:
            raise MalformedPayloadError(f"calendar has a property outside any component: {name}")
    if root is None or root.name != "VCALENDAR" or stack:
        raise MalformedPayloadError("payload is not a complete VCALENDAR")
    return root


# A parameter value may be quoted, and quoted values may contain `;`, `:` and `,`.
_PARAM = re.compile(r';([A-Za-z0-9-]+)=("[^"]*"|[^";:]*)')
_NAME = re.compile(r"[A-Za-z0-9-]+")


def _content_line(line: str) -> tuple[str, dict[str, str], str]:
    name_match = _NAME.match(line)
    if name_match is None:
        raise MalformedPayloadError(f"calendar has an unreadable line: {line[:40]!r}")
    position = name_match.end()
    params: dict[str, str] = {}
    while (param := _PARAM.match(line, position)) is not None:
        params[param[1].upper()] = param[2].strip('"')
        position = param.end()
    if not line.startswith(":", position):
        raise MalformedPayloadError(f"calendar has an unreadable line: {line[:40]!r}")
    return name_match[0].upper(), params, line[position + 1 :]


_ESCAPE = re.compile(r"\\([\\;,nN])")


def _unescape(value: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m[1] in "nN" else m[1], value)
