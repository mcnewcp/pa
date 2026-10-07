"""The storyline spec: the hand-written YAML ground truth of the synthetic corpus.

A spec lists the cast, the calendars, and the storylines, each with its dated events. Every
event names its channel (email, calendar event, calendar update, daily note section, or a
correction to a daily note), its participants, its time, and what happens (`what`).

Times without a UTC offset are read in the spec's `timezone`. People are referred to by cast
id (meaning their first email address) or by one of their email addresses.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pa_core.errors import PaError
from pa_core.owner import Owner

NOTE_CAPTURE_TIME = dt.time(1, 0)  # local time, the night after the note's day


class SpecError(PaError):
    """A storyline spec that cannot be read or is inconsistent."""


# Event ids name payload files, so they are kept filename-safe.
Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Person(_Model):
    """A cast member: a person, or an organization that sends email."""

    id: Id
    name: str
    aliases: list[str] = []
    emails: Annotated[list[str], Field(min_length=1)]


class Calendar(_Model):
    id: Id
    name: str
    # The calendar's own time zone (iCloud's X-WR-TIMEZONE); the spec's timezone if unset.
    timezone: str | None = None


class EmailEvent(_Model):
    id: Id
    channel: Literal["email"]
    at: dt.datetime
    sender: str = Field(alias="from")
    to: Annotated[list[str], Field(min_length=1)]
    cc: list[str] = []
    # Unset on a reply: "Re: " plus the subject of the message it replies to.
    subject: str | None = None
    reply_to: str | None = None
    what: str


class CalendarEvent(_Model):
    """A calendar event as first added. `at` is when it was added to the calendar.

    A `start` that is a date (no time) makes an all-day event; its `end`, if given, is the last
    day it covers.
    """

    id: Id
    channel: Literal["calendar"]
    at: dt.datetime
    calendar: str
    title: str
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date | None = None
    location: str | None = None
    description: str | None = None
    organizer: str | None = None
    attendees: list[str] = []
    what: str


class CalendarUpdate(_Model):
    """A new version of a calendar event: the fields given replace those of the version it updates.

    `updates` names the calendar event or the earlier update this one follows. `at` is when
    the change was made.
    """

    id: Id
    channel: Literal["calendar_update"]
    at: dt.datetime
    updates: str
    title: str | None = None
    start: dt.datetime | dt.date | None = None
    end: dt.datetime | dt.date | None = None
    location: str | None = None
    description: str | None = None
    organizer: str | None = None
    attendees: list[str] | None = None
    status: Literal["confirmed", "cancelled"] | None = None
    what: str


class NoteSection(_Model):
    """One topic, under its own heading, in the owner's daily note for `date`."""

    id: Id
    channel: Literal["note"]
    date: dt.date
    heading: str
    participants: list[str] = []
    what: str


class NoteCorrection(_Model):
    """A later correction to one daily note section, captured as a new version of that note."""

    id: Id
    channel: Literal["note_correction"]
    at: dt.datetime
    corrects: str
    what: str


type Event = EmailEvent | CalendarEvent | CalendarUpdate | NoteSection | NoteCorrection


class Storyline(_Model):
    id: Id
    title: str
    summary: str = ""
    events: list[Annotated[Event, Field(discriminator="channel")]]


class Address(_Model):
    name: str
    email: str


class StorylineSpec(_Model):
    owner: str
    timezone: str
    # The vault folder daily notes live in.
    vault_folder: str = "Daily"
    cast: list[Person]
    calendars: list[Calendar] = []
    storylines: list[Storyline]

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def events(self) -> list[Event]:
        """Every event, in the order the spec lists them."""
        return [event for storyline in self.storylines for event in storyline.events]

    def event(self, event_id: str) -> Event:
        return self._events_by_id[event_id]

    def find_event(self, event_id: str) -> Event | None:
        return self._events_by_id.get(event_id)

    def storyline_of(self, event_id: str) -> Storyline:
        return self._storyline_by_event[event_id]

    def calendar(self, calendar_id: str) -> Calendar:
        return {calendar.id: calendar for calendar in self.calendars}[calendar_id]

    def calendar_zone(self, calendar: Calendar) -> ZoneInfo:
        return ZoneInfo(calendar.timezone) if calendar.timezone else self.zone

    def person(self, ref: str) -> Person:
        """The cast member a reference (cast id or email address) names."""
        return self._people_by_ref[ref.lower()]

    def address(self, ref: str) -> Address:
        """The name and email address a reference names: a cast id means their first address."""
        person = self.person(ref)
        email = ref.lower() if "@" in ref else person.emails[0].lower()
        return Address(name=person.name, email=email)

    def local(self, moment: dt.datetime) -> dt.datetime:
        """A spec time as an aware datetime; a time without an offset is in the spec's zone."""
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=self.zone)

    def note_captured_at(self, day: dt.date) -> dt.datetime:
        """When the daily note for `day` is captured: the night after its day."""
        return dt.datetime.combine(day + dt.timedelta(days=1), NOTE_CAPTURE_TIME, tzinfo=self.zone)

    @property
    def owner_identity(self) -> Owner:
        """The corpus owner as ingest's owner configuration (PA_OWNER_NAME, PA_OWNER_EMAILS)."""
        person = self.person(self.owner)
        return Owner(
            name=person.name,
            email_addresses=tuple(email.lower() for email in person.emails),
            other_names=tuple(person.aliases),
        )

    @cached_property
    def _events_by_id(self) -> dict[str, Event]:
        return {event.id: event for event in self.events}

    @cached_property
    def _storyline_by_event(self) -> dict[str, Storyline]:
        return {event.id: storyline for storyline in self.storylines for event in storyline.events}

    @cached_property
    def _people_by_ref(self) -> dict[str, Person]:
        refs: dict[str, Person] = {}
        for person in self.cast:
            refs[person.id] = person
            for email in person.emails:
                refs[email.lower()] = person
        return refs

    @model_validator(mode="after")
    def _check(self) -> StorylineSpec:
        problems = list(_problems(self))
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_spec(path: Path) -> StorylineSpec:
    """Read and check a storyline spec; raises SpecError saying what is wrong."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SpecError(f"cannot read storyline spec {path}: {error}") from None
    try:
        return StorylineSpec.model_validate(data)
    except ValidationError as error:
        raise SpecError(f"storyline spec {path} is invalid:\n{error}") from None


def _problems(spec: StorylineSpec) -> Iterator[str]:
    """Everything inconsistent in a spec whose fields each parsed."""
    yield from _duplicates("cast id", [person.id for person in spec.cast])
    yield from _duplicates(
        "email address", [email.lower() for person in spec.cast for email in person.emails]
    )
    yield from _duplicates("calendar id", [calendar.id for calendar in spec.calendars])
    yield from _duplicates("event id", [event.id for event in spec.events])
    for zone in [spec.timezone, *(c.timezone for c in spec.calendars if c.timezone)]:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            yield f"unknown time zone {zone!r}"
            return
    people = spec._people_by_ref
    if spec.owner not in {person.id for person in spec.cast}:
        yield f"owner {spec.owner!r} is not in the cast"
    calendars = {calendar.id for calendar in spec.calendars}
    events = spec._events_by_id
    updated: set[str] = set()
    headings: set[tuple[dt.date, str]] = set()
    for event in spec.events:
        for ref in _people_refs(event):
            if ref.lower() not in people:
                yield f"event {event.id}: {ref!r} is not a cast id or a cast email address"
        match event:
            case EmailEvent(reply_to=parent_id) if parent_id is not None:
                parent = events.get(parent_id)
                if not isinstance(parent, EmailEvent):
                    yield f"event {event.id}: reply_to {parent_id!r} is not an email event"
                elif spec.local(parent.at) >= spec.local(event.at):
                    yield f"event {event.id}: it is sent before {parent_id}, which it replies to"
            case EmailEvent(subject=None):
                yield f"event {event.id}: it needs a subject, since it is not a reply"
            case CalendarEvent():
                if event.calendar not in calendars:
                    yield f"event {event.id}: calendar {event.calendar!r} is not in calendars"
                yield from _span_problems(event.id, event.start, event.end)
            case CalendarUpdate():
                previous = events.get(event.updates)
                if not isinstance(previous, CalendarEvent | CalendarUpdate):
                    yield f"event {event.id}: updates {event.updates!r} is not a calendar event"
                    continue
                if event.updates in updated:
                    yield f"event {event.id}: {event.updates} is already updated by another event"
                updated.add(event.updates)
                if spec.local(previous.at) >= spec.local(event.at):
                    yield f"event {event.id}: it is made before {event.updates}, which it updates"
            case NoteSection():
                if (event.date, event.heading) in headings:
                    yield f"event {event.id}: the {event.date} note already has {event.heading!r}"
                headings.add((event.date, event.heading))
            case NoteCorrection():
                section = events.get(event.corrects)
                if not isinstance(section, NoteSection):
                    yield f"event {event.id}: corrects {event.corrects!r} is not a note event"
                elif spec.local(event.at) <= spec.note_captured_at(section.date):
                    yield f"event {event.id}: it is made before the {section.date} note is captured"
            case _:
                pass
    # Fields an update leaves unset come from the version it updates, so a version's span
    # is only known once its chain of updates is resolved.
    for event in spec.events:
        if isinstance(event, CalendarUpdate):
            try:
                version = calendar_version(spec, event)
            except ValueError:
                continue  # a broken or circular chain is reported above
            yield from _span_problems(event.id, version.start, version.end)


@dataclass(frozen=True)
class CalendarVersion:
    """One version of a calendar event, with every field resolved through its updates."""

    event: CalendarEvent  # the event as first added
    sequence: int  # 0 as first added, then one more per update
    modified_at: dt.datetime
    title: str
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date | None
    location: str | None
    description: str | None
    organizer: str | None
    attendees: list[str]
    status: Literal["confirmed", "cancelled"]


def calendar_version(spec: StorylineSpec, event: CalendarEvent | CalendarUpdate) -> CalendarVersion:
    """The version of a calendar event that `event` adds or makes.

    Raises ValueError if its chain of updates does not lead back to a calendar event.
    """
    chain: list[CalendarUpdate] = []
    current: Event | None = event
    while isinstance(current, CalendarUpdate):
        if any(update.id == current.id for update in chain):
            raise ValueError(f"event {event.id}: its updates go round in a circle")
        chain.append(current)
        current = spec.find_event(current.updates)
    if not isinstance(current, CalendarEvent):
        raise ValueError(f"event {event.id}: its updates do not lead back to a calendar event")
    version = CalendarVersion(
        event=current,
        sequence=0,
        modified_at=current.at,
        title=current.title,
        start=current.start,
        end=current.end,
        location=current.location,
        description=current.description,
        organizer=current.organizer,
        attendees=current.attendees,
        status="confirmed",
    )
    for update in reversed(chain):
        changes = {
            name: getattr(update, name)
            for name in ("title", "location", "description", "organizer", "attendees", "status")
            if getattr(update, name) is not None
        }
        if update.start is not None:
            # A new start without a new end drops the old end, which may no longer fit.
            changes.update(start=update.start, end=update.end)
        elif update.end is not None:
            changes.update(end=update.end)
        version = replace(version, sequence=version.sequence + 1, modified_at=update.at, **changes)
    return version


def _span_problems(
    event_id: str, start: dt.datetime | dt.date, end: dt.datetime | dt.date | None
) -> Iterator[str]:
    all_day = not isinstance(start, dt.datetime)
    if end is None:
        return
    if all_day != (not isinstance(end, dt.datetime)):
        yield f"event {event_id}: start and end must both be dates or both be times"
    elif all_day and end < start:
        yield f"event {event_id}: its last day is before its first"
    elif not all_day and _comparable(end) <= _comparable(start):  # type: ignore[arg-type]
        yield f"event {event_id}: it ends before it starts"


def _comparable(moment: dt.datetime) -> dt.datetime:
    # Only for ordering a start and end within one event, both in the same zone if naive.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)


def _people_refs(event: Event) -> list[str]:
    match event:
        case EmailEvent():
            return [event.sender, *event.to, *event.cc]
        case CalendarEvent():
            return [*([event.organizer] if event.organizer else []), *event.attendees]
        case CalendarUpdate():
            return [*([event.organizer] if event.organizer else []), *(event.attendees or [])]
        case NoteSection():
            return event.participants
        case NoteCorrection():
            return []


def _duplicates(what: str, values: list[str]) -> Iterator[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            yield f"{what} {value!r} appears more than once"
        seen.add(value)
