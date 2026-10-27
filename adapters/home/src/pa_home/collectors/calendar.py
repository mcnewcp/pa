"""The iCloud Calendar collector: the owner's calendars, over CalDAV, as the server serves them.

The calendars are chosen by display name. Each resource lands exactly as served, behind a
capture header naming its calendar and when it was read (ADR-0008):

    Calendar: <the calendar's display name>
    Captured-At: <when the bytes were read, with its offset>
    <blank line>
    <the resource's bytes>

A collection reads each calendar's changes since its sync token; a token the server refuses
leads to a full comparison instead. Reconciliation, which the nightly job asks for, is always a
full comparison: every resource and its etag against what the collector last captured. A
resource gone from its calendar lands as a deletion: its last-known bytes again, under their
`Captured-At`, with `Deleted-At` added. So the collector keeps each captured resource's etag and
bytes, in a state file only the owner can read.

A backfill takes every resource with an occurrence on or after its window start (a series that
began long before, or anything upcoming), and leaves the rest alone, then and in every
collection after, unless they change into the window. A resource the normalizer can't read is
taken anyway, so ingest quarantines it where it can be seen.

Nothing here prints, and no failure reason holds a summary, a time, a UID or an href; only the
configured calendar names, the request that failed, and its HTTP status.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict

from pa_core.envelope import Source
from pa_core.errors import MalformedPayloadError
from pa_core.normalizers.calendar import occurs_on_or_after
from pa_home.collectors import CollectionError, SourceInbox, read_state, write_state
from pa_home.collectors.calendar_server import (
    CalendarServer,
    CalendarServerError,
    Collection,
    SyncTokenRejected,
)
from pa_home.config import ConfigError

_STATE = "calendars.json"

type _Pass = Literal["collect", "reconcile", "backfill"]


class Captured(BaseModel):
    """A resource as the collector last captured it."""

    # Its bytes are saved in base64: they need not be text, and they're content.
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    etag: str | None
    captured_at: AwareDatetime
    """When those bytes were read: their `Captured-At`."""
    data: bytes
    """The bytes as served, for the deletion that lands them again."""


class CalendarState(BaseModel):
    sync_token: str | None = None
    """The calendar's sync token when the last collection read it."""
    captured: dict[str, Captured] = {}
    """Each captured resource, by href."""
    passed_over: dict[str, str | None] = {}
    """Each resource left out for having no occurrence in the backfill's window, by href, with
    its etag then: it's looked at again only once its etag changes."""


class CalendarsState(BaseModel):
    window_start: AwareDatetime | None = None
    """The last backfill's window start: what has no occurrence since is left alone."""
    calendars: dict[str, CalendarState] = {}
    """Each calendar's state, by its display name."""


class CalendarCollector:
    """Collects the calendars named `calendars` from the account `connect` opens.

    The account is opened at the start of each pass, so a credential that's missing or
    exposed fails the calendar's collection alone, as a reason without content.
    """

    source = Source.ICLOUD_CALENDAR

    def __init__(
        self,
        connect: Callable[[], CalendarServer],
        calendars: Sequence[str],
        time_zone: ZoneInfo,
    ) -> None:
        self.connect = connect
        self.calendars = tuple(dict.fromkeys(calendars))
        self.time_zone = time_zone

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        self._run(inbox, state, "collect", None, now)

    def reconcile(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        """Compare every resource each calendar holds, and its etag, against what was captured:
        what changed lands, even if no sync report said so, and what's gone lands as a
        deletion."""
        self._run(inbox, state, "reconcile", None, now)

    def backfill(
        self,
        inbox: SourceInbox,
        state: Path,
        *,
        window_start: datetime,
        now: Callable[[], datetime],
    ) -> None:
        self._run(inbox, state, "backfill", window_start, now)

    def _run(
        self,
        inbox: SourceInbox,
        state: Path,
        kind: _Pass,
        window_start: datetime | None,
        now: Callable[[], datetime],
    ) -> None:
        saved = read_state(state / _STATE, CalendarsState)
        if window_start is not None:
            saved = saved.model_copy(update={"window_start": window_start})
        try:
            server = self.connect()
        except ConfigError as error:
            raise CollectionError(str(error)) from None
        collections = _served(server, self.calendars)
        failures: list[str] = []
        for name in self.calendars:
            collection = collections[name]
            if not isinstance(collection, Collection):
                failures.append(f"{name}: {collection}")
                continue
            before = saved.calendars.get(name)
            if before is None and kind != "backfill":
                failures.append(f"{name}: no sync token yet: run a backfill first")
                continue
            before = before or CalendarState()
            capture = _CalendarCapture(server, inbox, collection, before, self.time_zone, now)
            sync_token = before.sync_token
            try:
                sync_token = capture.run(kind, saved.window_start)
            except CalendarServerError as error:
                failures.append(f"{name}: {_reason(error)}")
            finally:
                # What landed is kept even when the pass stopped part way; its token isn't.
                calendars = {**saved.calendars, name: capture.state(sync_token)}
                saved = saved.model_copy(update={"calendars": calendars})
                write_state(state / _STATE, saved)
        write_state(state / _STATE, saved)
        if failures:
            raise CollectionError("; ".join(failures))


class _CalendarCapture:
    """One pass over one calendar."""

    def __init__(
        self,
        server: CalendarServer,
        inbox: SourceInbox,
        collection: Collection,
        before: CalendarState,
        time_zone: ZoneInfo,
        now: Callable[[], datetime],
    ) -> None:
        self.server = server
        self.inbox = inbox
        self.collection = collection
        self.sync_token = before.sync_token
        self.captured = dict(before.captured)
        self.passed_over = dict(before.passed_over)
        self.time_zone = time_zone
        self.now = now

    def run(self, kind: _Pass, window_start: datetime | None) -> str | None:
        """Land what changed in the calendar and what's gone from it, and return the calendar's
        sync token as of the pass."""
        if kind == "collect" and self.sync_token is not None:
            try:
                changes = self.server.changes(self.collection, self.sync_token)
            except SyncTokenRejected:
                pass  # Expired: compare everything instead.
            else:
                changed = {
                    href: etag
                    for href, etag in changes.changed.items()
                    if etag is None or etag != self._known(href)
                }
                self._land(changed, changes.removed, window_start)
                return changes.sync_token
        members = self.server.members(self.collection)
        # A backfill looks again at what an earlier one left out: its window may be wider.
        known = {} if kind == "backfill" else dict(self.passed_over)
        known.update({href: captured.etag for href, captured in self.captured.items()})
        changed = {
            href: etag
            for href, etag in members.etags.items()
            if href not in known or etag is None or etag != known[href]
        }
        gone = [href for href in [*self.captured, *self.passed_over] if href not in members.etags]
        self._land(changed, gone, window_start)
        return members.sync_token

    def state(self, sync_token: str | None) -> CalendarState:
        return CalendarState(
            sync_token=sync_token, captured=self.captured, passed_over=self.passed_over
        )

    def _known(self, href: str) -> str | None:
        if href in self.captured:
            return self.captured[href].etag
        return self.passed_over.get(href)

    def _land(
        self,
        changed: Mapping[str, str | None],
        removed: Iterable[str],
        window_start: datetime | None,
    ) -> None:
        if changed:
            read_at = self.now()
            for resource in self.server.fetch(self.collection, list(changed)):
                href = resource.href
                etag = resource.etag or changed.get(href)
                if href not in self.captured and not _in_window(resource.data, window_start):
                    self.passed_over[href] = etag
                    continue
                self._write(href, resource.data, read_at)
                self.captured[href] = Captured(etag=etag, captured_at=read_at, data=resource.data)
                self.passed_over.pop(href, None)
        for href in removed:
            self.passed_over.pop(href, None)
            last = self.captured.pop(href, None)
            if last is not None:
                self._write(href, last.data, last.captured_at, deleted_at=self.now())

    def _write(
        self, href: str, data: bytes, read_at: datetime, *, deleted_at: datetime | None = None
    ) -> None:
        name = " ".join((self.collection.name or "").split())
        header = f"Calendar: {name}\nCaptured-At: {self._moment(read_at)}\n"
        if deleted_at is not None:
            header += f"Deleted-At: {self._moment(deleted_at)}\n"
        # Named by its href's digest: an href can hold a UID, which can hold an address.
        stem = hashlib.sha256(href.encode()).hexdigest()[:16]
        self.inbox.write(stem, f"{header}\n".encode() + data)

    def _moment(self, moment: datetime) -> str:
        return moment.astimezone(self.time_zone).isoformat(timespec="seconds")


def _in_window(data: bytes, window_start: datetime | None) -> bool:
    """Whether a resource not captured yet is taken: anything with an occurrence on or after
    the window start, and anything the normalizer can't read, so it's quarantined in sight."""
    if window_start is None:
        return True
    try:
        return occurs_on_or_after(data, window_start)
    except MalformedPayloadError:
        return True


def _served(server: CalendarServer, names: Iterable[str]) -> dict[str, Collection | str]:
    """Each named calendar's collection, or why it can't be read."""
    try:
        collections = server.collections()
    except CalendarServerError as error:
        raise CollectionError(_reason(error)) from None
    by_name = Counter(collection.name for collection in collections)
    found: dict[str, Collection | str] = {}
    for name in names:
        if by_name[name] == 0:
            found[name] = "the account serves no calendar by that name"
        elif by_name[name] > 1:
            found[name] = "the account serves several calendars by that name"
        else:
            found[name] = next(c for c in collections if c.name == name)
    return found


def _reason(error: CalendarServerError) -> str:
    if error.status == 401:
        return (
            f"{error}: iCloud refused the Apple ID or its app-specific password; save a new "
            "app-specific password (setup-wizard.sh --icloud)"
        )
    return str(error)


@dataclass(frozen=True)
class BackfillCount:
    """What a backfill would do with one calendar's resources."""

    resources: int
    in_window: int
    """Resources with an occurrence on or after the window start: taken."""
    before: int
    """Resources whose every occurrence ended before it: left out."""
    unreadable: int
    """Resources the normalizer can't read: taken, to be quarantined."""


def count_for_backfill(
    server: CalendarServer, calendars: Sequence[str], window_start: datetime
) -> dict[str, BackfillCount | str]:
    """For each named calendar, what a backfill from `window_start` would take, or why the
    calendar can't be read. Every resource is fetched, and nothing but counts comes back."""
    counts: dict[str, BackfillCount | str] = {}
    for name, collection in _served(server, dict.fromkeys(calendars)).items():
        if not isinstance(collection, Collection):
            counts[name] = collection
            continue
        try:
            listing = server.listing(collection)
        except CalendarServerError as error:
            counts[name] = _reason(error)
            continue
        tally = Counter[str]()
        for resource in listing.resources:
            try:
                tally["in" if occurs_on_or_after(resource.data, window_start) else "before"] += 1
            except MalformedPayloadError:
                tally["unreadable"] += 1
        counts[name] = BackfillCount(
            resources=len(listing.resources),
            in_window=tally["in"],
            before=tally["before"],
            unreadable=tally["unreadable"],
        )
    return counts
