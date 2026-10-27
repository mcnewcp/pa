"""Calendar normalizer: one iCalendar resource (`.ics`) from iCloud Calendar to an envelope.

It reads two shapes (ADR-0008):

- **An export**, as the frozen corpus has: one VEVENT, with its calendar's name in
  `X-WR-CALNAME`. Its version is the event's SEQUENCE and LAST-MODIFIED, and it normalizes
  exactly as it always has.
- **A captured resource**, as a CalDAV collector lands it: the bytes the server served, behind
  a capture header with `Calendar` and `Captured-At`, and `Deleted-At` once it's gone from its
  calendar.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_core.envelope import Envelope, Kind, Participant, Role, Source, TimeSpan, episode_id
from pa_core.errors import MalformedPayloadError
from pa_core.l0 import raw_ref
from pa_core.normalizers.capture_header import split_capture_header

EXTENSION = ".ics"


def normalize_calendar_event(raw: bytes, *, captured_at: datetime) -> Envelope:
    """The resource's envelope. A captured resource's `captured_at` is its capture header's,
    not the `captured_at` given."""
    if _is_export(raw):
        return _export(raw, captured_at)
    return _captured(raw)


def _is_export(raw: bytes) -> bool:
    """Whether `raw` is an export with no capture header: it opens with its BEGIN line."""
    return raw.lstrip()[:6].upper() == b"BEGIN:"


def _export(raw: bytes, captured_at: datetime) -> Envelope:
    calendar = _parse(raw)
    event = _only_event(calendar)
    native_id = event.text("UID")
    if not native_id:
        raise MalformedPayloadError("calendar event has no UID")
    calendar_zone = calendar.text("X-WR-TIMEZONE")
    start = _moment(event, "DTSTART", calendar_zone)
    if start is None:
        raise MalformedPayloadError("calendar event has no DTSTART")
    end = _moment(event, "DTEND", calendar_zone)
    eid = episode_id(Source.ICLOUD_CALENDAR, native_id, _version(event))
    occurred_at = TimeSpan(start=start, end=end)
    return _envelope(eid, native_id, event, occurred_at, captured_at, calendar.text("X-WR-CALNAME"))


def _captured(raw: bytes) -> Envelope:
    header, served = split_capture_header(raw, what="calendar resource", required=("Calendar",))
    calendar = _parse(served)
    master, native_id = _series_master(calendar)
    calendar_zone = calendar.text("X-WR-TIMEZONE")
    start = _moment(master, "DTSTART", calendar_zone)
    if start is None:
        raise MalformedPayloadError("calendar event has no DTSTART")
    # A series has no one end: its occurrences are listed in its body.
    recurring = "RRULE" in master.properties or "RDATE" in master.properties
    end = None if recurring else _end(master, start, calendar_zone)
    # A deletion is the last-known bytes landed again, as their own episode: its version is
    # the deleted one's, marked, and it was captured when the collector found it gone.
    deleted_at = header.time("Deleted-At")
    calendar_name = header.require("Calendar")
    version = _resource_version(calendar_name, calendar)
    eid = episode_id(
        Source.ICLOUD_CALENDAR, native_id, f"deleted:{version}" if deleted_at else version
    )
    return _envelope(
        eid,
        native_id,
        master,
        TimeSpan(start=start, end=end),
        deleted_at or header.captured_at,
        calendar_name,
        deleted=deleted_at is not None,
    )


def _resource_version(calendar_name: str, calendar: _Component) -> str:
    """Which version of a captured resource this is: a digest of its calendar's name and the
    content of every event in it, apart from what changes when nothing has (ADR-0008).

    Content, not just SEQUENCE and LAST-MODIFIED, because iCloud ends a series with an UNTIL
    and bumps neither (#85). The calendar's name, because an event moved to another calendar
    is a new resource there with the same UID and content. Left out:

    - DTSTAMP, which changes with every time the event is served;
    - alarms (VALARM), which are the owner's reminders on their own devices, not part of the
      event, and the envelope never shows them. A client may mark an alarm `ACKNOWLEDGED`
      (RFC 9074) when its reminder is dismissed, so keeping them could make a version of a
      series each time one of its reminders goes off;
    - the order of the events, which says nothing.
    """
    events = sorted(
        json.dumps(_content(event), ensure_ascii=False)
        for event in calendar.children
        if event.name == "VEVENT"
    )
    canonical = json.dumps([calendar_name, events], ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content(component: _Component) -> list[object]:
    """`component`'s properties but DTSTAMP, and its components but alarms, in a canonical
    form."""
    properties = [
        [name, [[sorted(prop.params.items()), prop.value] for prop in values]]
        for name, values in sorted(component.properties.items())
        if name != "DTSTAMP"
    ]
    children = [
        [child.name, _content(child)] for child in component.children if child.name != "VALARM"
    ]
    return [properties, children]


def _end(event: _Component, start: datetime, calendar_zone: str | None) -> datetime:
    """When a captured event ends: its DTEND, else its DURATION after its start, else as
    RFC 5545 has it: at its start for a time, at the next midnight for a date."""
    end = _moment(event, "DTEND", calendar_zone)
    if end is not None:
        return end
    duration = event.text("DURATION")
    if duration is not None:
        return start + _duration(duration)
    start_value = event.first("DTSTART")
    assert start_value is not None
    is_date = len(start_value.value.strip()) == len("YYYYMMDD")
    return start + timedelta(days=1) if is_date else start


# A DURATION (RFC 5545): weeks alone, or days and a time of hours, minutes and seconds.
_DURATION = re.compile(
    r"([+-]?)P(?:(\d+)W|(?=\d|T\d)(?:(\d+)D)?(?:T(?=\d)(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?)"
)


def _duration(value: str) -> timedelta:
    match = _DURATION.fullmatch(value.upper())
    if match is None:
        raise MalformedPayloadError("calendar event has an unreadable DURATION")
    sign, weeks, days, hours, minutes, seconds = match.groups()
    length = timedelta(
        weeks=int(weeks or 0),
        days=int(days or 0),
        hours=int(hours or 0),
        minutes=int(minutes or 0),
        seconds=int(seconds or 0),
    )
    return -length if sign == "-" else length


def _series_master(calendar: _Component) -> tuple[_Component, str]:
    """The event a captured resource is about, and its UID: the resource's one event, or the
    master of its series.

    A resource holds one event, or a series: its master and an override (`RECURRENCE-ID`) for
    each occurrence changed, all with one UID.
    """
    events = [child for child in calendar.children if child.name == "VEVENT"]
    if not events:
        raise MalformedPayloadError("calendar resource has no events")
    uids = {event.text("UID") or "" for event in events}
    if "" in uids:
        raise MalformedPayloadError("calendar event has no UID")
    if len(uids) > 1:
        raise MalformedPayloadError(
            f"calendar resource has events with {len(uids)} UIDs; expected one series"
        )
    masters = [event for event in events if "RECURRENCE-ID" not in event.properties]
    if len(masters) != 1:
        raise MalformedPayloadError(
            f"calendar resource has {len(masters)} events without a RECURRENCE-ID; "
            "expected one series master"
        )
    return masters[0], uids.pop()


def _envelope(
    eid: str,
    native_id: str,
    event: _Component,
    occurred_at: TimeSpan,
    captured_at: datetime,
    calendar: str | None,
    *,
    deleted: bool = False,
) -> Envelope:
    return Envelope.seal(
        episode_id=eid,
        source=Source.ICLOUD_CALENDAR,
        kind=Kind.CALENDAR_EVENT,
        native_id=native_id,
        occurred_at=occurred_at,
        captured_at=captured_at,
        participants=_participants(event),
        calendar_name=calendar,
        subject=event.text("SUMMARY"),
        thread_ref=native_id,
        body=_body(event, deleted=deleted),
        raw_ref=raw_ref(Source.ICLOUD_CALENDAR, occurred_at.start, eid, EXTENSION),
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


def _body(event: _Component, *, deleted: bool = False) -> str:
    """The event's status, location and repeat rule as labelled lines, then its description.
    A deleted event's status is `deleted`."""
    status = "deleted" if deleted else event.text("STATUS")
    fields = [
        ("Status", status.lower() if status else None),
        ("Location", event.text("LOCATION")),
        ("Repeats", event.text("RRULE")),
    ]
    header = "\n".join(f"{label}: {value}" for label, value in fields if value)
    description = (event.text("DESCRIPTION") or "").replace("\r\n", "\n")
    return "\n\n".join(part for part in (header, description) if part)


def _version(event: _Component) -> str:
    """Which version of the event this is: its sequence number and last-modified time.

    DTSTAMP is left out because it changes with every export of the same version.
    """
    return f"{event.text('SEQUENCE') or '0'}:{event.text('LAST-MODIFIED') or ''}"


def _only_event(calendar: _Component) -> _Component:
    events = [child for child in calendar.children if child.name == "VEVENT"]
    if len(events) != 1:
        raise MalformedPayloadError(f"calendar has {len(events)} events; expected exactly one")
    return events[0]


# A DATE-TIME (`20261017T090000`, `Z` for UTC) or, for all-day events, a DATE (`20261017`).
_DATE_TIME = re.compile(r"(\d{8})(?:T(\d{6}))?(Z?)")


def _moment(event: _Component, name: str, calendar_zone: str | None) -> datetime | None:
    """A DATE or DATE-TIME property as an aware datetime, if present.

    A time keeps the zone the source gave it. A date, or a time with no zone, is read in the
    calendar's own time zone, else UTC.
    """
    prop = event.first(name)
    if prop is None:
        return None
    match = _DATE_TIME.fullmatch(prop.value.strip())
    try:
        if match is None or (match[3] and not match[2]):
            raise ValueError
        naive = datetime.strptime(match[1] + (match[2] or "000000"), "%Y%m%d%H%M%S")
    except ValueError:
        raise MalformedPayloadError(f"calendar event has an unreadable {name}") from None
    if match[3]:
        return naive.replace(tzinfo=UTC)
    return naive.replace(tzinfo=_zone(prop.params.get("TZID") or calendar_zone))


def _zone(name: str | None) -> tzinfo:
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise MalformedPayloadError("calendar names an unknown time zone") from None


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
    # Folding counts octets, so a fold can split a UTF-8 character: unfold before decoding.
    try:
        unfolded = re.sub(rb"\r?\n[ \t]", b"", raw).decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPayloadError(f"calendar is not UTF-8 ({error})") from None
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
            raise MalformedPayloadError("calendar has a property outside any component")
    if root is None or root.name != "VCALENDAR" or stack:
        raise MalformedPayloadError("payload is not a complete VCALENDAR")
    return root


# A parameter is one or more comma-separated values. A value may be quoted, and quoted values
# may contain `;`, `:` and `,`.
_PARAM_VALUE = r'(?:"[^"]*"|[^";:,]*)'
_PARAM = re.compile(rf";([A-Za-z0-9-]+)=({_PARAM_VALUE}(?:,{_PARAM_VALUE})*)")
_QUOTED = re.compile(r'"([^"]*)"')
_NAME = re.compile(r"[A-Za-z0-9-]+")


def _content_line(line: str) -> tuple[str, dict[str, str], str]:
    name_match = _NAME.match(line)
    if name_match is None:
        raise MalformedPayloadError("calendar has an unreadable line")
    position = name_match.end()
    params: dict[str, str] = {}
    while (param := _PARAM.match(line, position)) is not None:
        quoted = _QUOTED.fullmatch(param[2])
        params[param[1].upper()] = quoted[1] if quoted else param[2]
        position = param.end()
    if not line.startswith(":", position):
        raise MalformedPayloadError("calendar has an unreadable line")
    return name_match[0].upper(), params, line[position + 1 :]


_ESCAPE = re.compile(r"\\([\\;,nN])")


def _unescape(value: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m[1] in "nN" else m[1], value)
