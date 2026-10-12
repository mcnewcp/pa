"""The storyline spec: the hand-written YAML ground truth of the synthetic corpus.

A spec lists the cast, the calendars, and the storylines, each with its dated events. Every
event names its channel (email, calendar event, calendar update, daily note section, a
correction to a daily note, or an exchange with the assistant), its participants, its time, and
what happens (`what`). Background sections describe bulk mail (who sends it, how often, and
what kind), which is expanded into dated emails with a fixed seed.

A spec may span several files: a base spec and extensions that add to it (see `load_spec`).

Times without a UTC offset are read in the spec's `timezone`. People are referred to by cast
id (meaning their first email address) or by one of their email addresses.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_evals.models import FrozenModel

NOTE_CAPTURE_TIME = dt.time(1, 0)  # local time, the night after the note's day
BULK_HOURS = (6, 22)  # bulk mail is sent from 6:00 to 21:59 local time, never in a DST gap


class SpecError(PaError):
    """A storyline spec that cannot be read or is inconsistent."""


# Event ids name payload files, so they are kept filename-safe.
Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class Person(FrozenModel):
    """A cast member: a person, or an organization that sends email."""

    id: Id
    name: str
    aliases: list[str] = []
    emails: Annotated[list[str], Field(min_length=1)]


class CastAddition(FrozenModel):
    """More names and email addresses for someone already in the cast, as an extension adds."""

    id: Id
    aliases: list[str] = []
    emails: list[str] = []


class Calendar(FrozenModel):
    id: Id
    name: str
    # The calendar's own time zone (iCloud's X-WR-TIMEZONE); the spec's timezone if unset.
    timezone: str | None = None


type GmailCategory = Literal["promotions", "social", "updates", "forums"]
"""The Gmail category (inbox tab) a received email is sorted into; none means Primary."""


class EmailEvent(FrozenModel):
    id: Id
    channel: Literal["email"]
    at: dt.datetime
    sender: str = Field(alias="from")
    to: Annotated[list[str], Field(min_length=1)]
    cc: list[str] = []
    # Unset on a reply: "Re: " plus the subject of the message it replies to.
    subject: str | None = None
    reply_to: str | None = None
    category: GmailCategory | None = None
    # A test of the assistant's defences: it plants instructions or claims in what it says.
    adversarial: bool = False
    what: str


class CalendarEvent(FrozenModel):
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


class CalendarUpdate(FrozenModel):
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


class NoteSection(FrozenModel):
    """One topic, under its own heading, in the owner's daily note for `date`."""

    id: Id
    channel: Literal["note"]
    date: dt.date
    heading: str
    participants: list[str] = []
    what: str


class NoteCorrection(FrozenModel):
    """A later correction to one daily note section, captured as a new version of that note."""

    id: Id
    channel: Literal["note_correction"]
    at: dt.datetime
    corrects: str
    what: str


class ChatEvent(FrozenModel):
    """One exchange between the owner and the assistant: the owner's message and its reply.

    `at` is when the owner sends the message. Exchanges with the same `session` are one
    conversation, each following on from the ones before it; an exchange with no session is a
    conversation of its own.
    """

    id: Id
    channel: Literal["assistant_chat"]
    at: dt.datetime
    session: Id | None = None
    what: str


type StorylineEvent = (
    EmailEvent | CalendarEvent | CalendarUpdate | NoteSection | NoteCorrection | ChatEvent
)


class Storyline(FrozenModel):
    id: Id
    title: str
    summary: str = ""
    events: list[Annotated[StorylineEvent, Field(discriminator="channel")]]


class BulkSender(FrozenModel):
    """A sender of bulk mail, how often it writes (a rough rate), and what kinds of mail."""

    id: Id
    name: str
    email: str
    category: GmailCategory | None = None
    per_week: Annotated[float, Field(gt=0)]
    kinds: Annotated[list[str], Field(min_length=1)]


class Background(FrozenModel):
    """Bulk mail, from `start` to `end` (both included), expanded into emails with `seed`.

    Each sender writes about `per_week` emails a week, give or take a fifth, at random days
    and waking hours, each of one of its kinds. The same section and seed always expand to the
    same emails. They go to `to`, or to the owner if unset.
    """

    id: Id
    title: str
    summary: str = ""
    seed: int
    start: dt.date
    end: dt.date
    to: list[str] = []
    senders: Annotated[list[BulkSender], Field(min_length=1)]


@dataclass(frozen=True)
class BulkEmail:
    """One email a background section expands into. Its subject and body are the model's."""

    id: str
    background: str  # the background section's id
    at: dt.datetime  # local time, in the spec's zone
    sender: str  # the bulk sender's id
    to: tuple[str, ...]
    category: GmailCategory | None
    kind: str


type Event = StorylineEvent | BulkEmail


class Address(FrozenModel):
    name: str
    email: str


class StorylineSpec(FrozenModel):
    owner: str
    timezone: str
    # The vault folder daily notes live in.
    vault_folder: str = "Daily"
    cast: list[Person]
    # Added after a person's own, so a cast id still means their first address.
    cast_additions: list[CastAddition] = []
    calendars: list[Calendar] = []
    storylines: list[Storyline]
    background: list[Background] = []
    # Set when the spec is a base spec with extensions (see `load_spec`).
    _base: BaseSpec | None = PrivateAttr(default=None)

    @cached_property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @cached_property
    def events(self) -> list[Event]:
        """Every event, in the order the spec lists them, then the bulk mail."""
        storylines = [event for storyline in self.storylines for event in storyline.events]
        return [*storylines, *self.bulk_emails]

    @cached_property
    def bulk_emails(self) -> list[BulkEmail]:
        """The emails every background section expands into, section by section, in time order."""
        return [email for section in self.background for email in _expand(section, self.owner)]

    def event(self, event_id: str) -> Event:
        return self._events_by_id[event_id]

    def find_event(self, event_id: str) -> Event | None:
        return self._events_by_id.get(event_id)

    def storyline_of(self, event_id: str) -> Storyline:
        return self._storyline_by_event[event_id]

    @property
    def adversarial_ids(self) -> list[str]:
        """The event ids of the adversarial emails, so checks can find their episodes."""
        return [e.id for e in self.events if isinstance(e, EmailEvent) and e.adversarial]

    @property
    def base(self) -> BaseSpec | None:
        """The base spec this spec's extensions extend; None for a spec with no extensions."""
        return self._base

    def in_base(self, event_id: str) -> bool:
        """Whether the event is the base spec's, not an extension's (False if not extended)."""
        part = self._part_of_event.get(event_id)
        return self._base is not None and part in self._base.parts

    def calendar(self, calendar_id: str) -> Calendar:
        return self._calendars_by_id[calendar_id]

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
            email_addresses=tuple(person.emails),
            other_names=tuple(person.aliases),
        )

    @cached_property
    def _events_by_id(self) -> dict[str, Event]:
        return {event.id: event for event in self.events}

    @cached_property
    def _calendars_by_id(self) -> dict[str, Calendar]:
        return {calendar.id: calendar for calendar in self.calendars}

    @cached_property
    def _storyline_by_event(self) -> dict[str, Storyline]:
        return {event.id: storyline for storyline in self.storylines for event in storyline.events}

    @cached_property
    def _part_of_event(self) -> dict[str, str]:
        """The storyline or background section each event is part of, by event id."""
        parts = {event_id: storyline.id for event_id, storyline in self._storyline_by_event.items()}
        parts.update((email.id, email.background) for email in self.bulk_emails)
        return parts

    @cached_property
    def people(self) -> list[Person]:
        """Everyone who sends or receives anything: the cast, then the bulk senders.

        Each cast member has any names and addresses an extension adds to them.
        """
        additions: dict[str, list[CastAddition]] = defaultdict(list)
        for addition in self.cast_additions:
            additions[addition.id].append(addition)
        cast = [
            person.model_copy(
                update={
                    "aliases": [*person.aliases, *(a for x in more for a in x.aliases)],
                    "emails": [*person.emails, *(e for x in more for e in x.emails)],
                }
            )
            if (more := additions.get(person.id))
            else person
            for person in self.cast
        ]
        senders = [
            Person(id=sender.id, name=sender.name, emails=[sender.email])
            for section in self.background
            for sender in section.senders
        ]
        return [*cast, *senders]

    @cached_property
    def _people_by_ref(self) -> dict[str, Person]:
        refs: dict[str, Person] = {}
        for person in self.people:
            refs[person.id] = person
            for email in person.emails:
                refs[email.lower()] = person
        return refs

    @model_validator(mode="after")
    def _check(self, info: ValidationInfo) -> StorylineSpec:
        self._base = (info.context or {}).get("base")
        problems = list(_problems(self))
        if problems:
            raise ValueError("; ".join(problems))
        return self


@dataclass(frozen=True)
class BaseSpec:
    """The base spec of an extended spec: its file's name, and the ids of its storylines and
    background sections."""

    name: str
    parts: frozenset[str]


class SpecExtension(FrozenModel):
    """A storyline spec file that extends a base spec, adding to its lists.

    The base spec sets the owner, time zone, and vault folder; an extension only adds.
    """

    cast: list[Person] = []
    cast_additions: list[CastAddition] = []
    calendars: list[Calendar] = []
    storylines: list[Storyline] = []
    background: list[Background] = []


def load_spec(path: Path, *extensions: Path) -> StorylineSpec:
    """Read and check a storyline spec; raises SpecError saying what is wrong.

    The spec is the base spec at `path` plus any `extensions`, files beside it that each add
    cast, calendars, storylines, and background sections to it. They are checked together, so
    an id may be used in only one of them, and any of them may refer to what another defines.

    The base spec's payloads are frozen, so an extension may not change them or their episode
    ids: it adds no events to the base spec's threads (replies to its emails, updates of its
    calendar events, corrections of its notes), and no sections to a day the base spec has a
    daily note for.
    """
    data = _read(path)
    base = _validate(StorylineSpec, data, str(path))
    if not extensions:
        return base
    merged = dict(data)
    for extension in extensions:
        more = _read(extension)
        _validate(SpecExtension, more, str(extension))
        for key in SpecExtension.model_fields:
            merged[key] = [*merged.get(key, []), *more.get(key, [])]
    where = f"{path} with {', '.join(str(extension) for extension in extensions)}"
    parts = [*(storyline.id for storyline in base.storylines), *(s.id for s in base.background)]
    base_spec = BaseSpec(path.name, frozenset(parts))
    return _validate(StorylineSpec, merged, where, context={"base": base_spec})


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SpecError(f"cannot read storyline spec {path}: {error}") from None


def _validate[T: FrozenModel](
    model: type[T], data: Any, where: str, context: dict[str, Any] | None = None
) -> T:
    try:
        return model.model_validate(data, context=context)
    except ValidationError as error:
        raise SpecError(f"storyline spec {where} is invalid:\n{error}") from None


def _problems(spec: StorylineSpec) -> Iterator[str]:
    """Everything inconsistent in a spec whose fields each parsed."""
    # Bulk senders are named like the cast, so their ids and addresses must differ from it.
    yield from _duplicates("cast id", [person.id for person in spec.people])
    yield from _duplicates(
        "email address", [email.lower() for person in spec.people for email in person.emails]
    )
    cast_ids = {person.id for person in spec.cast}
    for addition in spec.cast_additions:
        if addition.id not in cast_ids:
            yield f"cast_additions: {addition.id!r} is not in the cast"
    yield from _duplicates("calendar id", [calendar.id for calendar in spec.calendars])
    yield from _duplicates(
        "storyline id", [part.id for part in [*spec.storylines, *spec.background]]
    )
    yield from _duplicates("event id", [event.id for event in spec.events])
    for section in spec.background:
        if section.end < section.start:
            yield f"background {section.id}: it ends before it starts"
        for ref in section.to:
            if ref.lower() not in spec._people_by_ref:
                yield f"background {section.id}: {ref!r} is not a cast id or a cast email address"
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
        if (
            isinstance(event, EmailEvent)
            and event.category
            and event.sender.lower() in people
            and people[event.sender.lower()].id == spec.owner
        ):
            yield f"event {event.id}: the owner's sent mail has no Gmail category"
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
    yield from _extension_problems(spec)


def _extension_problems(spec: StorylineSpec) -> Iterator[str]:
    """How the extensions of a spec would change its base spec's frozen payloads, if they would."""
    base = spec.base
    if base is None:
        return
    note_days = {
        event.date
        for event in spec.events
        if isinstance(event, NoteSection) and spec.in_base(event.id)
    }
    for event in spec.events:
        if spec.in_base(event.id):
            continue
        match event:
            case EmailEvent(reply_to=str() as earlier):
                verb = "replies to"
            case CalendarUpdate(updates=earlier):
                verb = "updates"
            case NoteCorrection(corrects=earlier):
                verb = "corrects"
            case NoteSection() if event.date in note_days:
                yield (
                    f"event {event.id}: {base.name} already has a daily note for {event.date}; "
                    "an extension adds no sections to it, since that would change the note's "
                    "episode id"
                )
                continue
            case _:
                continue
        if spec.in_base(earlier):
            yield (
                f"event {event.id}: it {verb} {earlier}, from {base.name}; "
                "an extension adds no events to that file's threads"
            )


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
        case NoteCorrection() | ChatEvent():
            return []
        case BulkEmail():
            return []  # its sender is a bulk sender, and its section's recipients are checked


def _expand(section: Background, owner: str) -> list[BulkEmail]:
    """The emails a background section expands into, in time order; the same every time."""
    rng = random.Random(f"{section.id}:{section.seed}")
    days = (section.end - section.start).days + 1
    if days < 1:
        return []  # reported as a problem
    emails: list[BulkEmail] = []
    for sender in section.senders:
        count = max(1, round(sender.per_week * days / 7 * rng.uniform(0.8, 1.2)))
        drafts = sorted(
            (
                dt.datetime.combine(
                    section.start + dt.timedelta(days=rng.randrange(days)),
                    dt.time(rng.randrange(*BULK_HOURS), rng.randrange(60)),
                ),
                rng.choice(sender.kinds),
            )
            for _ in range(count)
        )
        emails += [
            BulkEmail(
                id=f"{section.id}-{sender.id}-{number:03d}",
                background=section.id,
                at=at,
                sender=sender.id,
                to=tuple(section.to or [owner]),
                category=sender.category,
                kind=kind,
            )
            for number, (at, kind) in enumerate(drafts, start=1)
        ]
    return sorted(emails, key=lambda email: (email.at, email.id))


def _duplicates(what: str, values: list[str]) -> Iterator[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            yield f"{what} {value!r} appears more than once"
        seen.add(value)
