"""Recurrence: a calendar series' occurrences, worked out so the assistant never has to.

A series is its first occurrence's start and length, an RRULE, any added dates (RDATE) and
removed ones (EXDATE), and its overrides: occurrences changed one at a time (ADR-0008). Times
are aware, in the event's own time zone, so an occurrence keeps its wall-clock time across a
daylight-saving change. An all-day event's times are midnights.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrule, rruleset, rrulestr

from pa_core.errors import MalformedPayloadError


@dataclass(frozen=True)
class Override:
    """One occurrence changed on its own: `recurrence_id` is when it was due."""

    recurrence_id: datetime
    start: datetime
    end: datetime
    cancelled: bool = False


@dataclass(frozen=True)
class Series:
    start: datetime
    length: timedelta
    rule: str | None
    added: tuple[datetime, ...] = ()
    removed: tuple[datetime, ...] = ()
    overrides: tuple[Override, ...] = ()
    all_day: bool = False

    def __post_init__(self) -> None:
        if self.rule is not None:
            _rule(self.rule, self.start)  # raises for a rule that can't be read


@dataclass(frozen=True)
class Occurrence:
    start: datetime
    end: datetime
    moved_from: datetime | None = None


@dataclass(frozen=True)
class OccurrenceWindow:
    """Which occurrences a series' body lists: those starting from `months_before` its anchor
    up to `months_after` it."""

    months_before: int = 2
    months_after: int = 6

    def around(self, anchor: datetime) -> tuple[datetime, datetime]:
        return (
            anchor - relativedelta(months=self.months_before),
            anchor + relativedelta(months=self.months_after),
        )


def occurrences_between(series: Series, start: datetime, end: datetime) -> list[Occurrence]:
    """The series' occurrences starting at or after `start` and before `end`, with its
    exclusions, added dates and overrides applied, in time order."""
    found = [
        Occurrence(moment, moment + series.length)
        for moment in _unchanged(series).between(start, end, inc=True)
        if moment < end
    ]
    found += [
        Occurrence(o.start, o.end, o.recurrence_id if o.start != o.recurrence_id else None)
        for o in _kept_overrides(series)
        if start <= o.start < end
    ]
    return sorted(found, key=lambda occurrence: occurrence.start)


def any_occurrence_on_or_after(series: Series, moment: datetime) -> bool:
    """Whether an occurrence of the series is still going at `moment` or starts after it."""
    if any(_on_or_after(o.start, o.end, moment) for o in _kept_overrides(series)):
        return True
    # An occurrence's length is in wall-clock time, so a day's margin covers any shift in it.
    for start in _unchanged(series).xafter(moment - series.length - timedelta(days=1), inc=True):
        if _on_or_after(start, start + series.length, moment):
            return True
    return False


def _on_or_after(start: datetime, end: datetime, moment: datetime) -> bool:
    return end > moment or start >= moment


def last_end(series: Series) -> datetime | None:
    """When the series' last occurrence ends: None when its rule has no COUNT or UNTIL."""
    if series.rule is not None and not {"COUNT", "UNTIL"} & dict(_parts(series.rule)).keys():
        return None
    ends = [start + series.length for start in _unchanged(series)]
    ends += [override.end for override in _kept_overrides(series)]
    return max(ends, default=None)


def _kept_overrides(series: Series) -> Iterator[Override]:
    return (override for override in series.overrides if not override.cancelled)


def _unchanged(series: Series) -> rruleset:
    """The occurrences the overrides leave as the rule has them."""
    moments = rruleset()
    # The first occurrence is always DTSTART, whether or not the rule would give it (RFC 5545).
    moments.rdate(series.start)
    if series.rule is not None:
        moments.rrule(_rule(series.rule, series.start))
    for added in series.added:
        moments.rdate(added)
    for removed in (*series.removed, *(o.recurrence_id for o in series.overrides)):
        moments.exdate(removed)
    return moments


def _rule(text: str, start: datetime) -> rrule:
    """The RRULE `text` for a series starting at `start`."""
    parts = [
        f"{name}={_utc(_until(value, start)) if name == 'UNTIL' else value}"
        for name, value in _parts(text)
    ]
    try:
        rule = rrulestr(";".join(parts), dtstart=start)
    except (ValueError, TypeError):
        rule = None
    if not isinstance(rule, rrule):
        raise MalformedPayloadError("calendar event has an unreadable RRULE")
    return rule


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parts(rule: str) -> list[tuple[str, str]]:
    """An RRULE's parts, in order, with their names in upper case."""
    parts = []
    for part in rule.split(";"):
        name, _, value = part.partition("=")
        parts.append((name.strip().upper(), value.strip()))
    return parts


def _until(value: str, start: datetime) -> datetime:
    """An RRULE's UNTIL, as an aware time.

    It's UTC when DTSTART has a time zone (RFC 5545), but some clients write it without one, as
    a local time or a date; that's read in the series' own time zone, a date at the series'
    time of day.
    """
    try:
        if value.upper().endswith("Z"):
            return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        if len(value) == len("YYYYMMDD"):
            local = datetime.strptime(value, "%Y%m%d").replace(
                hour=start.hour, minute=start.minute, second=start.second
            )
        else:
            local = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        raise MalformedPayloadError("calendar event has an unreadable RRULE") from None
    return local.replace(tzinfo=start.tzinfo)


# Words: what the series' body says of its recurrence, above the list of its occurrences.

_UNITS = {
    "SECONDLY": "second",
    "MINUTELY": "minute",
    "HOURLY": "hour",
    "DAILY": "day",
    "WEEKLY": "week",
    "MONTHLY": "month",
    "YEARLY": "year",
}
_WEEKDAYS = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}
_WEEKDAY = re.compile(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)")
# The parts the words cover besides which days: how often, how long. WKST changes nothing the
# words say.
_SPAN_PARTS = frozenset({"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST"})


def describe(series: Series) -> str:
    """The series' recurrence in words: how often, at what time, from when and until when.

    For example "every Tuesday and Thursday, 6 to 7 pm America/Chicago, from Sep 8, 2026 until
    Dec 15, 2026". Days the words don't cover keep the rule's parts as written.
    """
    if series.rule is None:
        words, rule = f"on {len(series.added) + 1} dates", {}
    else:
        parts = _parts(series.rule)
        words, rule = _how_often(parts, series.start), dict(parts)
    words += f", {_time_of_day(series)}, from {_date(series.start)}"
    if "UNTIL" in rule:
        until = _until(rule["UNTIL"], series.start).astimezone(series.start.tzinfo)
        words += f" until {_date(until)}"
    if "COUNT" in rule:
        words += ", once" if rule["COUNT"] == "1" else f", {rule['COUNT']} times"
    if series.rule is not None and series.added:
        others = len(series.added)
        words += f", and on {others} other date{'s' if others > 1 else ''}"
    return words


def _how_often(parts: list[tuple[str, str]], start: datetime) -> str:
    rule = dict(parts)
    frequency = rule.get("FREQ", "").upper()
    unit = _UNITS.get(frequency, frequency.lower())
    interval = int(rule.get("INTERVAL") or 1)
    every = f"every {unit}" if interval == 1 else f"every {interval} {unit}s"
    days = {name: value for name, value in parts if name not in _SPAN_PARTS}
    on = _which_days(frequency, days, start)
    if on is None:
        written = ";".join(f"{name}={value}" for name, value in days.items())
        return f"{every} ({written})"
    if not on:
        return every
    if interval == 1 and frequency in ("DAILY", "WEEKLY") and on.split()[-1] in _NAMES:
        return f"every {on}"
    return f"{every} on {on}"


_NAMES = frozenset(_WEEKDAYS.values())


def _which_days(frequency: str, days: dict[str, str], start: datetime) -> str | None:
    """Which days a rule falls on, in words: "" for every day its frequency gives, None for
    days the words don't cover."""
    weekdays = [_WEEKDAY.fullmatch(day.upper()) for day in days.get("BYDAY", "").split(",")]
    match frequency, sorted(days):
        case (("DAILY" | "HOURLY" | "MINUTELY" | "SECONDLY"), []):
            return ""
        case "WEEKLY", []:
            return _WEEKDAYS[f"{start:%a}"[:2].upper()]
        case "MONTHLY", []:
            return f"the {_nth(start.day)}"
        case "YEARLY", []:
            return f"{start:%b} {start.day}"
        case (("DAILY" | "WEEKLY"), ["BYDAY"]) if all(w and not w[1] for w in weekdays):
            return _and([_WEEKDAYS[w[2]] for w in weekdays if w])
        case "MONTHLY", ["BYDAY"] if all(w and w[1] and int(w[1]) in _ORDINALS for w in weekdays):
            return _and([f"the {_ORDINALS[int(w[1])]} {_WEEKDAYS[w[2]]}" for w in weekdays if w])
        case "MONTHLY", ["BYMONTHDAY"]:
            numbers = days["BYMONTHDAY"].split(",")
            if not all((n.isdigit() and 1 <= int(n) <= 31) or n == "-1" for n in numbers):
                return None
            return _and([f"the {_nth(int(n))}" if n != "-1" else "the last day" for n in numbers])
        case _:
            return None


def _nth(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _time_of_day(series: Series) -> str:
    """When in the day an occurrence is, in the series' time zone, or that it's all day."""
    if series.all_day:
        days = series.length.days
        return "all day" if days <= 1 else f"all day, {days} days"
    start, end = series.start, series.start + series.length
    first, first_half = _clock(start)
    zone = str(start.tzinfo)
    if end == start:
        return f"{first} {first_half} {zone}"
    last, last_half = _clock(end)
    if first_half == last_half and end.date() == start.date():
        return f"{first} to {last} {last_half} {zone}"
    return f"{first} {first_half} to {last} {last_half} {zone}"


def _clock(moment: datetime) -> tuple[str, str]:
    hour = moment.hour % 12 or 12
    minutes = f":{moment.minute:02}" if moment.minute else ""
    return f"{hour}{minutes}", "am" if moment.hour < 12 else "pm"


def _date(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment.year}"
