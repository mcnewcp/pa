"""The corpus generator: a storyline spec in, native-shaped raw payloads out.

Everything but the prose (senders, recipients, attendees, times, calendars, note dates) comes
straight from the spec. The model client writes only prose: email bodies and daily note text.
Each payload is written in its source's native shape (`.eml`, `.ics`, a captured Obsidian
daily note `.md`) to one flat directory, ready to be dropped into an inbox.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from email import policy
from email.headerregistry import Address as MailAddress
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from pydantic import BaseModel

from pa_core.errors import PaError
from pa_core.model_client import ModelClient
from pa_core.normalizers import normalizer_for
from pa_evals.storyline import (
    CalendarEvent,
    CalendarUpdate,
    CalendarVersion,
    EmailEvent,
    Event,
    NoteCorrection,
    NoteSection,
    StorylineSpec,
    calendar_version,
)

# Calendar UIDs live under a reserved domain so they can never collide with real ones.
UID_DOMAIN = "corpus.pa.invalid"


class GeneratedCorpusError(PaError):
    """The generated corpus cannot be written, or lacks the payload a spec event is in."""


class Prose(BaseModel):
    """What the model writes: plain text, nothing else."""

    text: str


def generate_corpus(spec: StorylineSpec, client: ModelClient, out_dir: Path) -> list[Path]:
    """Write a raw payload for every event in `spec` to `out_dir`, which must be empty.

    The model is asked for prose only. Nothing is written until all of it is generated, so a
    failed model call leaves `out_dir` as it was. Returns the payload files, in name order.
    """
    if out_dir.exists() and any(out_dir.iterdir()):
        raise GeneratedCorpusError(f"{out_dir} is not empty; delete it or choose another directory")
    payloads = _Generator(spec, client).payloads()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, raw in payloads.items():
        (out_dir / name).write_bytes(raw)
    return sorted(out_dir / name for name in payloads)


def payload_name(spec: StorylineSpec, event: Event) -> str:
    """The corpus file holding the payload where `event` first appears.

    Every section of a day's daily note is in that day's note; a correction is in the version
    of the note it produces.
    """
    match event:
        case EmailEvent():
            return f"{_stamp(spec.local(event.at))}_{event.id}.eml"
        case CalendarEvent() | CalendarUpdate():
            return f"{_stamp(spec.local(event.at))}_{event.id}.ics"
        case NoteSection():
            return f"{event.date.isoformat()}_daily-note.md"
        case NoteCorrection():
            return f"{_stamp(spec.local(event.at))}_{event.id}.md"


def evidence_ids(spec: StorylineSpec, corpus_dir: Path) -> dict[str, str]:
    """The episode id of every spec event, by event id, as ingest will assign it.

    Ids are worked out by ingest's own normalizers from the payload each event is in, so they
    always match. A daily note's id depends on its prose, so this reads the generated corpus.
    """
    ids: dict[str, str] = {}
    captured_at = dt.datetime.now(dt.UTC)
    for event in spec.events:
        name = payload_name(spec, event)
        try:
            raw = (corpus_dir / name).read_bytes()
        except OSError:
            raise GeneratedCorpusError(
                f"event {event.id}: its payload {name} is not in {corpus_dir}"
            ) from None
        normalize = normalizer_for(name, spec.owner_identity)
        ids[event.id] = normalize(raw, captured_at=captured_at).episode_id
    return ids


def _stamp(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H%M")


@dataclass
class _Section:
    heading: str
    text: str


class _Generator:
    def __init__(self, spec: StorylineSpec, client: ModelClient) -> None:
        self.spec = spec
        self.client = client

    def payloads(self) -> dict[str, bytes]:
        payloads: dict[str, bytes] = {}
        payloads.update(self._emails())
        for event in self.spec.events:
            if isinstance(event, CalendarEvent | CalendarUpdate):
                payloads[payload_name(self.spec, event)] = self._calendar(event)
        payloads.update(self._daily_notes())
        return dict(sorted(payloads.items()))

    def _prose(self, prompt: str) -> str:
        return self.client.generate(prompt, Prose).text.strip()

    # Email

    def _emails(self) -> dict[str, bytes]:
        spec = self.spec
        emails = [event for event in spec.events if isinstance(event, EmailEvent)]
        # A reply is written after the message it replies to, so its prose can follow on.
        emails.sort(key=lambda event: spec.local(event.at))
        bodies: dict[str, str] = {}
        subjects: dict[str, str] = {}
        payloads: dict[str, bytes] = {}
        for event in emails:
            parent = spec.event(event.reply_to) if event.reply_to else None
            assert parent is None or isinstance(parent, EmailEvent)
            subject = event.subject or _reply_subject(subjects[parent.id] if parent else "")
            subjects[event.id] = subject
            bodies[event.id] = self._prose(
                self._email_prompt(event, subject, parent, bodies.get(event.reply_to or ""))
            )
            payloads[payload_name(spec, event)] = self._email(event, subject, bodies[event.id])
        return payloads

    def _email(self, event: EmailEvent, subject: str, body: str) -> bytes:
        spec = self.spec
        message = EmailMessage(policy=policy.SMTP)
        message["X-Gmail-Labels"] = (
            "Sent" if spec.person(event.sender).id == spec.owner else "Inbox"
        )
        message["Message-ID"] = f"<{message_id(spec, event)}>"
        thread = _thread(spec, event)
        if thread:
            message["In-Reply-To"] = f"<{thread[-1]}>"
            message["References"] = " ".join(f"<{ref}>" for ref in thread)
        message["Date"] = format_datetime(spec.local(event.at))
        message["From"] = self._mail_addresses([event.sender])
        message["To"] = self._mail_addresses(event.to)
        if event.cc:
            message["Cc"] = self._mail_addresses(event.cc)
        message["Subject"] = subject
        message.set_content(body + "\n")
        return message.as_bytes()

    def _mail_addresses(self, refs: list[str]) -> tuple[MailAddress, ...]:
        addresses = (self.spec.address(ref) for ref in refs)
        return tuple(MailAddress(display_name=a.name, addr_spec=a.email) for a in addresses)

    def _email_prompt(
        self, event: EmailEvent, subject: str, parent: EmailEvent | None, parent_body: str | None
    ) -> str:
        spec = self.spec
        sender = spec.address(event.sender)
        lines = [
            self._preamble(event),
            "Write the plain-text body of one email.",
            "",
            f"From: {self._who(event.sender)}",
            f"To: {', '.join(self._who(ref) for ref in event.to)}",
        ]
        if event.cc:
            lines.append(f"Cc: {', '.join(self._who(ref) for ref in event.cc)}")
        lines += [
            f"Sent: {_spoken_time(spec.local(event.at))}",
            f"Subject: {subject}",
            f"What this email says: {event.what}",
        ]
        if parent is not None and parent_body is not None:
            lines += [
                "",
                f"It replies to this email from {spec.address(parent.sender).name}:",
                "---",
                parent_body,
                "---",
            ]
        lines += [
            "",
            f"Write in the voice of {sender.name}, with a greeting and sign-off if they would use",
            "them. Keep it short and natural. Write only the body: no headers, no subject line,",
            "and no quoted earlier messages. Say exactly what 'What this email says' says, and",
            "add no other dates, times, places, names, or decisions.",
        ]
        return "\n".join(lines)

    # Calendar

    def _calendar(self, event: CalendarEvent | CalendarUpdate) -> bytes:
        spec = self.spec
        version = calendar_version(spec, event)
        calendar = spec.calendar(version.event.calendar)
        zone = spec.calendar_zone(calendar)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//pa//synthetic corpus//EN",
            "CALSCALE:GREGORIAN",
            f"X-WR-CALNAME:{_ics_text(calendar.name)}",
            f"X-WR-TIMEZONE:{zone.key}",
            "BEGIN:VEVENT",
            f"UID:{calendar_uid(version)}",
            f"SEQUENCE:{version.sequence}",
            f"DTSTAMP:{_ics_utc(spec.local(version.modified_at))}",
            f"CREATED:{_ics_utc(spec.local(version.event.at))}",
            f"LAST-MODIFIED:{_ics_utc(spec.local(version.modified_at))}",
            *self._span_lines(version, zone),
            f"SUMMARY:{_ics_text(version.title)}",
        ]
        if version.location:
            lines.append(f"LOCATION:{_ics_text(version.location)}")
        if version.description:
            lines.append(f"DESCRIPTION:{_ics_text(version.description)}")
        if version.organizer:
            lines.append(f"ORGANIZER;{self._ics_user(version.organizer)}")
        lines += [f"ATTENDEE;{self._ics_user(ref)}" for ref in version.attendees]
        lines += [f"STATUS:{version.status.upper()}", "END:VEVENT", "END:VCALENDAR"]
        return "".join(f"{_fold(line)}\r\n" for line in lines).encode("utf-8")

    def _span_lines(self, version: CalendarVersion, zone: dt.tzinfo) -> list[str]:
        start, end = version.start, version.end
        if isinstance(start, dt.datetime):
            assert end is None or isinstance(end, dt.datetime)
            moments = [("DTSTART", start), *([("DTEND", end)] if end else [])]
            return [
                f"{name};TZID={zone}:{self.spec.local(moment).astimezone(zone):%Y%m%dT%H%M%S}"
                for name, moment in moments
            ]
        # An all-day event's DTEND is the day after its last day.
        last_day = end if end is not None else start
        return [
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{last_day + dt.timedelta(days=1):%Y%m%d}",
        ]

    def _ics_user(self, ref: str) -> str:
        address = self.spec.address(ref)
        return f"CN={_ics_param(address.name)}:mailto:{address.email}"

    # Daily notes

    def _daily_notes(self) -> dict[str, bytes]:
        spec = self.spec
        days: dict[dt.date, list[NoteSection]] = defaultdict(list)
        for event in spec.events:
            if isinstance(event, NoteSection):
                days[event.date].append(event)
        corrections = sorted(
            (event for event in spec.events if isinstance(event, NoteCorrection)),
            key=lambda event: spec.local(event.at),
        )
        payloads: dict[str, bytes] = {}
        notes: dict[dt.date, dict[str, _Section]] = {}
        for day in sorted(days):
            notes[day] = {
                section.id: _Section(section.heading, self._prose(self._note_prompt(section)))
                for section in days[day]
            }
            payloads[payload_name(spec, days[day][0])] = self._note(
                day, notes[day].values(), spec.note_captured_at(day)
            )
        for correction in corrections:
            section_event = spec.event(correction.corrects)
            assert isinstance(section_event, NoteSection)
            section = notes[section_event.date][section_event.id]
            section.text = self._prose(self._correction_prompt(section_event, section, correction))
            payloads[payload_name(spec, correction)] = self._note(
                section_event.date,
                notes[section_event.date].values(),
                spec.local(correction.at),
            )
        return payloads

    def _note(self, day: dt.date, sections: Iterable[_Section], captured_at: dt.datetime) -> bytes:
        body = "\n\n".join(f"## {section.heading}\n\n{section.text}" for section in sections)
        header = (
            f"Vault-Path: {self.spec.vault_folder}/{day.isoformat()}.md\n"
            f"Captured-At: {captured_at.isoformat()}\n"
        )
        return f"{header}\n{body}\n".encode()

    def _note_prompt(self, section: NoteSection) -> str:
        owner = self.spec.person(self.spec.owner)
        lines = [
            self._preamble(section),
            f"Write one section of {owner.name}'s Obsidian daily note for "
            f"{_spoken_date(section.date)}, under the heading '{section.heading}'.",
            "",
            f"What to record: {section.what}",
        ]
        if section.participants:
            lines.append(
                f"People involved: {', '.join(self._who(p) for p in section.participants)}"
            )
        lines += [
            "",
            f"Write it as {owner.name} would jot it in their own daily note: first person,",
            "terse, a few lines or bullet points. Call people what the owner would call them",
            "(first names or nicknames). Write only the section's text, without the heading.",
            "Record exactly what 'What to record' says, and add no other dates, times, places,",
            "names, or decisions.",
        ]
        return "\n".join(lines)

    def _correction_prompt(
        self, section_event: NoteSection, section: _Section, correction: NoteCorrection
    ) -> str:
        owner = self.spec.person(self.spec.owner)
        return "\n".join(
            [
                self._preamble(section_event),
                f"{owner.name}'s daily note for {_spoken_date(section_event.date)} has this "
                f"section under the heading '{section.heading}':",
                "---",
                section.text,
                "---",
                f"On {_spoken_time(self.spec.local(correction.at))} they corrected it: "
                f"{correction.what}",
                "",
                "Rewrite the section with the correction applied, keeping everything else as it",
                "is. Write only the section's text, without the heading.",
            ]
        )

    # Shared prompt parts

    def _preamble(self, event: Event) -> str:
        owner = self.spec.person(self.spec.owner)
        storyline = self.spec.storyline_of(event.id)
        summary = f" {storyline.summary}" if storyline.summary else ""
        # A storyline's summary often tells how it ends; prose written mid-story must not.
        return (
            f"You are writing part of a synthetic corpus: the fictional personal life of "
            f"{owner.name}.\n"
            f"Background, for context only: this is part of the storyline "
            f"'{storyline.title}'.{summary}\n"
            "The background may describe things that have not happened yet. Never mention "
            "anything from it that the instructions below do not say.\n"
        )

    def _who(self, ref: str) -> str:
        person = self.spec.person(ref)
        address = self.spec.address(ref)
        aliases = " or ".join(person.aliases)
        known_as = f", known to friends and family as {aliases}" if aliases else ""
        return f"{person.name} <{address.email}>{known_as}"


def message_id(spec: StorylineSpec, event: EmailEvent) -> str:
    """An email's internet message ID: its event id at the sender's domain."""
    return f"{event.id}@{spec.address(event.sender).email.rsplit('@', 1)[1]}"


def calendar_uid(version: CalendarVersion) -> str:
    """A calendar event's UID, the same for every version: its first event id."""
    return f"{version.event.id}@{UID_DOMAIN}"


def _thread(spec: StorylineSpec, event: EmailEvent) -> list[str]:
    """The message IDs this email follows, from the thread's first message to its parent."""
    thread: list[str] = []
    current = event
    while current.reply_to:
        parent = spec.event(current.reply_to)
        assert isinstance(parent, EmailEvent)
        thread.insert(0, message_id(spec, parent))
        current = parent
    return thread


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _spoken_date(day: dt.date) -> str:
    return f"{day:%A, %B} {day.day}, {day.year}"


def _spoken_time(moment: dt.datetime) -> str:
    return f"{_spoken_date(moment.date())} at {moment:%H:%M} ({moment.tzname()})"


# iCalendar (RFC 5545) text: escape backslash, semicolon, comma and newline.


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_param(value: str) -> str:
    """A parameter value, quoted when it holds a character that would end it early."""
    value = value.replace('"', "'")
    return f'"{value}"' if any(c in value for c in ";:,") else value


def _ics_utc(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """Fold a content line to 75 octets per line, never splitting a UTF-8 character."""
    parts: list[str] = []
    current = ""
    limit = 75
    for char in line:
        if len((current + char).encode("utf-8")) > limit:
            parts.append(current)
            current = ""
            limit = 74  # continuation lines start with a space
        current += char
    parts.append(current)
    return "\r\n ".join(parts)
