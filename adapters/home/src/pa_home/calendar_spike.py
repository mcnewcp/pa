"""The calendar spike (#85): the structure of the owner's real iCloud calendars, never their
content.

    python -m pa_home.calendar_spike calendars
    python -m pa_home.calendar_spike snapshot LABEL [--calendar NAME]...
    python -m pa_home.calendar_spike diff A B [--marker SUMMARY]
    python -m pa_home.calendar_spike clean

It reads CalDAV only, never writes it. Snapshots go to a scratch directory under the data root
(`scratch/calendar-spike/<label>/`), outside the store and the repository, and `clean` deletes
them. What it prints is structure: calendar display names (configuration, which the owner
chooses), counts, shapes, and yes-or-no answers. It never prints a summary, description,
location, attendee, UID, href, URL, etag, sync token, timestamp, or any other property value,
nor the credential; resources are referred to only in counts.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from pa_core.atomic import write_atomically
from pa_core.errors import PaError
from pa_core.inspection import inspect_structure
from pa_core.normalizers.calendar import _Component, _parse  # pyright: ignore[reportPrivateUsage]
from pa_home.collectors.calendar_server import (
    CalDavServer,
    CalendarServer,
    CalendarServerError,
    Collection,
    Listing,
    SyncTokenRejected,
)
from pa_home.config import ICloudCredentials, load_data_root, load_icloud_credentials

Connect = Callable[[ICloudCredentials], CalendarServer]
SCRATCH = Path("scratch") / "calendar-spike"
"""Where snapshots go, under the data root."""
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class SpikeError(PaError):
    """The spike can't go on. Its message holds no calendar content."""


def _connect(credentials: ICloudCredentials) -> CalendarServer:
    return CalDavServer(credentials.username, credentials.password)


def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Connect = _connect,
    environ: Mapping[str, str] = os.environ,
) -> int:
    parser = argparse.ArgumentParser(
        prog="calendar-spike",
        description="The structure of the owner's iCloud calendars, read over CalDAV, never "
        "their content.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "calendars", help="List the calendars the account serves, and whether Birthdays is one."
    )
    snapshot = commands.add_parser(
        "snapshot",
        help="Fetch every resource of the calendars into scratch under the data root, and "
        "print their structure.",
    )
    snapshot.add_argument("label", help="A name for the snapshot, such as baseline.")
    snapshot.add_argument(
        "--calendar",
        action="append",
        default=[],
        metavar="NAME",
        help="A calendar to take, by display name; repeat for several. Default: every calendar "
        "that holds events.",
    )
    diff = commands.add_parser(
        "diff",
        help="Compare two snapshots: the listings, the sync report from the first's token, "
        "and the test event.",
    )
    diff.add_argument("a", help="The earlier snapshot's label.")
    diff.add_argument("b", help="The later snapshot's label.")
    diff.add_argument(
        "--marker",
        help="The test event's SUMMARY. Only whether each event's SUMMARY equals it is used.",
    )
    commands.add_parser("clean", help="Delete every snapshot, and the spike's scratch directory.")
    args = parser.parse_args(argv)
    try:
        if args.command == "clean":
            _clean(_spike_directory(environ))
        if args.command == "calendars":
            _calendars(connect(load_icloud_credentials(environ)))
        elif args.command == "snapshot":
            directory = _spike_directory(environ)
            target = _snapshot_directory(directory, args.label)
            server = connect(load_icloud_credentials(environ))
            _snapshot(server, directory, target, args.label, args.calendar)
        elif args.command == "diff":
            directory = _spike_directory(environ)
            _diff(
                directory,
                _load(directory, args.a),
                _load(directory, args.b),
                args.marker,
                lambda: connect(load_icloud_credentials(environ)),
            )
    except PaError as error:
        print(f"calendar-spike: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Its message, and a traceback's, can hold a URL naming the account, or content.
        print(f"calendar-spike: unexpected {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


def _calendars(server: CalendarServer) -> None:
    collections = server.collections()
    print(f"calendars: {len(collections)}")
    for number, collection in enumerate(collections, start=1):
        print(f"  {number}. {_name(collection)}: {_describe(collection)}")
    birthdays = [c for c in collections if c.name and "birthday" in c.name.lower()]
    served = ", ".join(_name(c) for c in birthdays)
    print(f"birthdays calendar: {f'served ({served})' if birthdays else 'not served'}")


def _name(collection: Collection) -> str:
    return collection.name or "(no display name)"


_KINDS = frozenset(
    {"shared", "shared-owner", "subscribed", "schedule-inbox", "schedule-outbox", "notification"}
    | {"dropbox", "principal"}
)
"""The resource types a calendar collection is known to carry besides `calendar`: WebDAV's,
CalDAV's, and calendarserver's sharing and subscriptions. Any other is counted, not printed."""


def _describe(collection: Collection) -> str:
    components = [c for c in collection.components if c in _COMPONENTS]
    if others := len(collection.components) - len(components):
        components.append(f"{others} other")
    kinds = [kind for kind in collection.kinds if kind in _KINDS]
    if others := len(collection.kinds) - len(kinds):
        kinds.append(f"{others} other {'kind' if others == 1 else 'kinds'}")
    parts = ["/".join(components) or "components unstated", *kinds]
    if collection.writable is not None:
        parts.append("writable" if collection.writable else "read-only")
    return ", ".join(parts)


# Snapshots: each calendar's resources as served, with their hrefs and etags and the calendar's
# sync token, kept in scratch for diffs. Only their structure is printed.


class _Kept(BaseModel):
    href: str
    etag: str | None
    file: str


class _KeptChanges(BaseModel):
    """A sync report from an earlier snapshot's token, or why there isn't one."""

    since: str
    """The earlier snapshot's label."""
    changed: dict[str, str | None] = {}
    removed: list[str] = []
    refused: int | None = None
    """The HTTP status the server refused the token with."""
    failed: str | None = None
    """Why the report failed otherwise, as the server error says it (no content)."""


class _KeptCalendar(BaseModel):
    url: str
    name: str | None
    sync_token: str | None
    etag_mismatches: int = 0
    resources: list[_Kept]
    changes: _KeptChanges | None = None
    """The sync report from the previous snapshot's token, taken with this one."""


class _Snapshot(BaseModel):
    label: str
    order: int
    calendars: list[_KeptCalendar]


_RECORD = "snapshot.json"


def _spike_directory(environ: Mapping[str, str]) -> Path:
    """The spike's scratch directory, in a data root that already exists: the spike never
    creates one, which the setup wizard does with its own layout and modes."""
    root = load_data_root(environ).path
    if not root.is_dir():
        raise SpikeError(f"the data root {root} doesn't exist; create it, or set PA_DATA_DIR")
    return root / SCRATCH


def _snapshot_directory(directory: Path, label: str) -> Path:
    if not _LABEL.fullmatch(label):
        raise SpikeError("a label is letters, digits, - and _, such as baseline or moved-1")
    target = directory / label
    if target.exists():
        raise SpikeError(f"snapshot {label} already exists; clean, or choose another label")
    return target


def _snapshot(
    server: CalendarServer, directory: Path, target: Path, label: str, names: list[str]
) -> None:
    chosen = _choose(server.collections(), names)
    _private_directory(directory)
    earlier = _snapshots(directory)
    previous = earlier[-1] if earlier else None
    partial = directory / f".{label}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(mode=0o700)
    kept: list[_KeptCalendar] = []
    listings: list[tuple[str, Listing]] = []
    for index, collection in enumerate(chosen, start=1):
        listing = server.listing(collection)
        resources: list[_Kept] = []
        for number, resource in enumerate(listing.resources, start=1):
            file = f"calendar-{index}/{number}.ics"
            write_atomically(partial / file, resource.data, mode=0o600)
            resources.append(_Kept(href=resource.href, etag=resource.etag, file=file))
        before = _calendar(previous, collection.url) if previous else None
        kept.append(
            _KeptCalendar(
                url=collection.url,
                name=collection.name,
                sync_token=listing.sync_token,
                etag_mismatches=listing.etag_mismatches,
                resources=resources,
                changes=_changes(server, collection, previous.label, before.sync_token)
                if previous and before and before.sync_token
                else None,
            )
        )
        listings.append((_display_names(chosen)[index - 1], listing))
    record = _Snapshot(label=label, order=len(earlier) + 1, calendars=kept)
    write_atomically(partial / _RECORD, record.model_dump_json(indent=1).encode(), mode=0o600)
    for path in partial.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
    partial.rename(target)
    print(f"snapshot {label}: {_count(len(chosen), 'calendar')}")
    for name, listing in listings:
        print(f"calendar {name}")
        for line in _structure_lines(listing):
            print(f"  {line}")


def _choose(collections: list[Collection], names: list[str]) -> list[Collection]:
    if not names:
        return [c for c in collections if not c.components or "VEVENT" in c.components]
    chosen: list[Collection] = []
    for name in names:
        named = [c for c in collections if c.name == name]
        if not named:
            raise SpikeError(f"no calendar is named {name}; `calendars` lists them")
        chosen += [c for c in named if c not in chosen]
    return chosen


def _display_names(collections: list[Collection]) -> list[str]:
    """Each collection's name, numbered where several share one."""
    totals = Counter(_name(c) for c in collections)
    seen: Counter[str] = Counter()
    names: list[str] = []
    for collection in collections:
        name = _name(collection)
        seen[name] += 1
        names.append(name if totals[name] == 1 else f"{name} #{seen[name]}")
    return names


def _snapshots(directory: Path) -> list[_Snapshot]:
    """The snapshots taken so far, oldest first."""
    found = [
        _Snapshot.model_validate_json((path / _RECORD).read_bytes())
        for path in (directory.iterdir() if directory.is_dir() else ())
        if (path / _RECORD).is_file() and not path.name.startswith(".")
    ]
    return sorted(found, key=lambda snapshot: snapshot.order)


def _changes(
    server: CalendarServer, collection: Collection, since: str, sync_token: str
) -> _KeptChanges:
    """The sync report from `since`'s token, or why the server gave none."""
    try:
        changes = server.changes(collection, sync_token)
    except SyncTokenRejected as error:
        return _KeptChanges(since=since, refused=error.status)
    except CalendarServerError as error:
        return _KeptChanges(since=since, failed=str(error))
    return _KeptChanges(since=since, changed=changes.changed, removed=changes.removed)


def _calendar(snapshot: _Snapshot, url: str) -> _KeptCalendar | None:
    return next((calendar for calendar in snapshot.calendars if calendar.url == url), None)


def _load(directory: Path, label: str) -> _Snapshot:
    record = directory / label / _RECORD
    if not _LABEL.fullmatch(label) or not record.is_file():
        raise SpikeError(f"no snapshot {label}; take it with `snapshot {label}`")
    return _Snapshot.model_validate_json(record.read_bytes())


def _clean(directory: Path) -> None:
    if not directory.exists():
        print("clean: nothing to delete")
        return
    count = len(_snapshots(directory))
    shutil.rmtree(directory)
    print(f"clean: deleted {_count(count, 'snapshot')}")


def _private_directory(directory: Path) -> None:
    """Makes `directory` and its parents up to the data root, readable only by the owner."""
    for path in (directory.parent, directory):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)


# Structure: what shape each resource has, counted, never what it says.

_SHAPES = (
    "no events",
    "single event",
    "series",
    "series with overrides",
    "event with overrides but no RRULE or RDATE",
    "overrides without their series",
    "several series of one UID",
    "several UIDs",
)
_RECURRENCE = ("RRULE", "EXDATE", "RDATE")
_VERSIONS = ("SEQUENCE", "LAST-MODIFIED")
_EXPECTED_COMPONENTS = frozenset({"VCALENDAR", "VEVENT", "VTIMEZONE", "STANDARD", "DAYLIGHT"})
_OTHER_COMPONENTS = frozenset(
    {"VALARM", "VTODO", "VJOURNAL", "VFREEBUSY", "VAVAILABILITY", "AVAILABLE", "VLOCATION"}
    | {"VRESOURCE", "PARTICIPANT"}
)
_COMPONENTS = _EXPECTED_COMPONENTS | _OTHER_COMPONENTS
"""The iCalendar component names that may be printed; any other is counted."""


@dataclass
class _Structure:
    resources: int = 0
    with_etag: int = 0
    unreadable: int = 0
    events_per_resource: Counter[int] = field(default_factory=Counter)
    shapes: Counter[str] = field(default_factory=Counter)
    overrides_per_series: Counter[int] = field(default_factory=Counter)
    resources_with: Counter[str] = field(default_factory=Counter)
    events: int = 0
    events_with: Counter[str] = field(default_factory=Counter)
    other_components: Counter[str] = field(default_factory=Counter)


def _structure_lines(listing: Listing) -> list[str]:
    structure = _Structure(resources=len(listing.resources))
    for resource in listing.resources:
        structure.with_etag += resource.etag is not None
        try:
            calendar = _parse(resource.data)
        except Exception:
            structure.unreadable += 1
            continue
        _count_resource(calendar, structure)
    token = "given" if listing.sync_token else "none"
    lines = [
        f"resources: {structure.resources} (sync token: {token}; "
        f"etags: {structure.with_etag} of {structure.resources})"
    ]
    if structure.unreadable:
        lines.append(f"unreadable: {structure.unreadable}")
    if listing.etag_mismatches:
        lines.append(f"etag changed between listing and fetching: {listing.etag_mismatches}")
    lines.append(
        "events per resource: "
        + _distribution(structure.events_per_resource, lambda n: _count(n, "event"))
    )
    lines.append("shapes:" if +structure.shapes else "shapes: none")
    for shape in _SHAPES:
        if count := structure.shapes[shape]:
            overrides = ""
            if shape == "series with overrides":
                spread = _distribution(structure.overrides_per_series, str, joiner=" in ")
                overrides = f" (overrides: {spread})"
            lines.append(f"  {shape}: {count}{overrides}")
    lines.append(
        "resources with "
        + ", ".join(f"{name}: {structure.resources_with[name]}" for name in _RECURRENCE)
    )
    lines.append(
        "resources with "
        + ", ".join(
            f"{name}: {structure.resources_with[name]}" for name in ("VTIMEZONE", "X-WR-CALNAME")
        )
    )
    lines.append(
        f"events: {structure.events}, with "
        + ", ".join(f"{name}: {structure.events_with[name]}" for name in _VERSIONS)
    )
    others = ", ".join(
        f"{name} {count}" for name, count in sorted(structure.other_components.items())
    )
    lines.append(f"other components: {others or 'none'}")
    return lines


def _count_resource(calendar: _Component, structure: _Structure) -> None:
    events = [child for child in calendar.children if child.name == "VEVENT"]
    structure.events_per_resource[len(events)] += 1
    structure.events += len(events)
    shape, overrides = _shape(events)
    structure.shapes[shape] += 1
    if shape == "series with overrides":
        structure.overrides_per_series[overrides] += 1
    for name in _RECURRENCE:
        structure.resources_with[name] += any(name in event.properties for event in events)
    for name in _VERSIONS:
        structure.events_with[name] += sum(name in event.properties for event in events)
    structure.resources_with["VTIMEZONE"] += any(c.name == "VTIMEZONE" for c in calendar.children)
    structure.resources_with["X-WR-CALNAME"] += "X-WR-CALNAME" in calendar.properties
    for component in _walk(calendar):
        if component.name not in _EXPECTED_COMPONENTS:
            known = component.name in _OTHER_COMPONENTS or component.name.startswith("X-")
            structure.other_components[component.name if known else "(other)"] += 1


def _shape(events: list[_Component]) -> tuple[str, int]:
    """A resource's shape, and how many overrides it holds."""
    if not events:
        return "no events", 0
    if len({event.text("UID") for event in events}) > 1:
        return "several UIDs", 0
    masters = [event for event in events if "RECURRENCE-ID" not in event.properties]
    overrides = len(events) - len(masters)
    if not masters:
        return "overrides without their series", overrides
    if len(masters) > 1:
        return "several series of one UID", overrides
    recurs = any(name in masters[0].properties for name in ("RRULE", "RDATE"))
    if not overrides:
        return ("series" if recurs else "single event"), 0
    if not recurs:
        return "event with overrides but no RRULE or RDATE", overrides
    return "series with overrides", overrides


def _walk(component: _Component) -> Iterator[_Component]:
    yield component
    for child in component.children:
        yield from _walk(child)


def _distribution(counts: Counter[int], label: Callable[[int], str], joiner: str = ": ") -> str:
    return ", ".join(f"{label(key)}{joiner}{counts[key]}" for key in sorted(counts)) or "none"


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


# Diffs: what changed between two snapshots, by the listings and by the sync report, and how
# the test event changed. Resources are matched by href and compared by bytes and etag; only
# the counts and the yes-or-no answers are printed.


def _diff(
    directory: Path,
    a: _Snapshot,
    b: _Snapshot,
    marker: str | None,
    connect: Callable[[], CalendarServer],
) -> None:
    print(f"diff {a.label} {b.label}")
    servers: list[CalendarServer] = []

    def server() -> CalendarServer:
        if not servers:
            servers.append(connect())
        return servers[0]

    for calendar in b.calendars:
        before = _calendar(a, calendar.url)
        print(f"calendar {calendar.name or '(no display name)'}")
        if before is None:
            print(f"  not in {a.label}")
            continue
        for line in _calendar_diff(directory, a, before, b, calendar, server):
            print(f"  {line}")
    for calendar in a.calendars:
        if _calendar(b, calendar.url) is None:
            print(f"calendar {calendar.name or '(no display name)'}")
            print(f"  not in {b.label}")
    if marker is not None:
        for line in _test_event(directory, a, b, marker):
            print(line)


def _calendar_diff(
    directory: Path,
    a: _Snapshot,
    before: _KeptCalendar,
    b: _Snapshot,
    after: _KeptCalendar,
    server: Callable[[], CalendarServer],
) -> list[str]:
    old = {kept.href: kept for kept in before.resources}
    new = {kept.href: kept for kept in after.resources}
    added = new.keys() - old.keys()
    removed = old.keys() - new.keys()
    etag_new = bytes_new_only = 0
    changed: set[str] = set()
    for href in new.keys() & old.keys():
        same_bytes = _bytes(directory, a, old[href]) == _bytes(directory, b, new[href])
        same_etag = old[href].etag == new[href].etag
        if same_bytes and same_etag:
            continue
        changed.add(href)
        etag_new += not same_etag
        bytes_new_only += same_etag
    unchanged = len(new) - len(added) - len(changed)
    lines = [
        f"listing: added {len(added)}, changed {len(changed)}, removed {len(removed)}, "
        f"unchanged {unchanged}"
    ]
    if changed:
        lines.append(
            f"changed: etag new {etag_new}, bytes new under the same etag {bytes_new_only}"
        )
    if before.sync_token and after.sync_token:
        same = before.sync_token == after.sync_token
        lines.append(f"sync token: {'same' if same else 'changed'}")
    else:
        lines.append("sync token: not given")
    report = after.changes if after.changes and after.changes.since == a.label else None
    when = f"taken with {b.label}"
    if report is None and before.sync_token:
        when = "taken now"
        collection = Collection(url=after.url, name=after.name)
        report = _changes(server(), collection, a.label, before.sync_token)
    if report is None:
        return lines
    heading = f"sync report from {a.label}'s token, {when}"
    if report.refused is not None:
        return [*lines, f"{heading}: refused (HTTP {report.refused})"]
    if report.failed is not None:
        return [*lines, f"{heading}: failed: {report.failed}"]
    reported, gone = set(report.changed), set(report.removed)
    lines.append(f"{heading}: changed {len(reported)}, removed {len(gone)}")
    disagreements = {
        "changed in the listing, not reported": len((added | changed) - reported),
        "reported changed, unchanged in the listing": len(reported - added - changed),
        "removed in the listing, not reported": len(removed - gone),
        "reported removed, still listed": len(gone & new.keys()),
    }
    never_listed = len(gone - old.keys() - new.keys())
    found = [f"{what}: {count}" for what, count in disagreements.items() if count]
    also = [f"also reported removed, never listed: {never_listed}"] if never_listed else []
    verdict = "no" if found else "yes"
    detail = f" ({'; '.join(found + also)})" if found or also else ""
    lines.append(f"sync report agrees with the listing: {verdict}{detail}")
    return lines


def _bytes(directory: Path, snapshot: _Snapshot, kept: _Kept) -> bytes:
    return (directory / snapshot.label / kept.file).read_bytes()


# The test event: the resource whose events have the marker as their SUMMARY.


@dataclass
class _Found:
    calendar: str
    href: str
    etag: str | None
    raw: bytes
    events: list[_Component]


def _test_event(directory: Path, a: _Snapshot, b: _Snapshot, marker: str) -> list[str]:
    before, after = _find(directory, a, marker), _find(directory, b, marker)
    lines = [
        f"test event (SUMMARY is the marker): in {a.label} {len(before)}, in {b.label} {len(after)}"
    ]
    if len(before) > 1 or len(after) > 1:
        return [*lines, "  the marker matches several resources: choose a distinctive one"]
    if not before and not after:
        return lines
    if not before:
        found = after[0]
        return [
            *lines,
            f"  added in {b.label}, calendar {found.calendar}",
            f"  events: {_events_summary(found.events)}",
            f"  structure in {b.label}:",
            *_inspected(found.raw),
        ]
    if not after:
        return [*lines, f"  removed in {b.label}, calendar {before[0].calendar}"]
    old, new = before[0], after[0]
    place = "changed in place" if old.href == new.href else "moved to another resource"
    calendar = (
        f"calendar {new.calendar}"
        if old.calendar == new.calendar
        else f"calendar {old.calendar} -> {new.calendar}"
    )
    etag = "same" if old.etag == new.etag else "new"
    lines += [
        f"  {place}, {calendar}; etag {etag}",
        f"  events: {len(old.events)} -> {len(new.events)} "
        f"({_shape(old.events)[0]} -> {_shape(new.events)[0]}), "
        f"EXDATEs: {_exdates(old.events)} -> {_exdates(new.events)}",
        f"  content apart from DTSTAMP: {_changed(_content(old.events) != _content(new.events))}; "
        "version (every event's SEQUENCE and LAST-MODIFIED): "
        f"{_changed(_version(old.events) != _version(new.events))}",
    ]
    lines += _event_changes(old.events, new.events)
    return [*lines, f"  structure in {b.label}:", *_inspected(new.raw)]


def _find(directory: Path, snapshot: _Snapshot, marker: str) -> list[_Found]:
    found: list[_Found] = []
    for calendar in snapshot.calendars:
        for kept in calendar.resources:
            raw = _bytes(directory, snapshot, kept)
            try:
                parsed = _parse(raw)
            except Exception:
                continue
            events = [child for child in parsed.children if child.name == "VEVENT"]
            if any(event.text("SUMMARY") == marker.strip() for event in events):
                name = calendar.name or "(no display name)"
                found.append(_Found(name, kept.href, kept.etag, raw, events))
    return found


def _event_changes(old: list[_Component], new: list[_Component]) -> list[str]:
    """How the series and each override changed, and whether each change bumped a version."""
    before, after = _by_occurrence(old), _by_occurrence(new)
    unbumped = 0
    lines: list[str] = []
    if None in before and None in after:
        master_old, master_new = before[None], after[None]
        changed = _event_content(master_old) != _event_content(master_new)
        bump = _bump(master_old, master_new)
        unbumped += changed and not bump.bumped
        moved, rerule = (
            _changed(_text(master_old, name) != _text(master_new, name))
            for name in ("DTSTART", "RRULE")
        )
        detail = f"changed (DTSTART {moved}, RRULE {rerule})" if changed else "unchanged"
        lines.append(f"  series: {detail}; {bump.describe()}")
    old_overrides = {key for key in before if key is not None}
    new_overrides = {key for key in after if key is not None}
    added = new_overrides - old_overrides
    kept = old_overrides & new_overrides
    changed_overrides = 0
    for key in kept:
        if _event_content(before[key]) != _event_content(after[key]):
            changed_overrides += 1
            unbumped += not _bump(before[key], after[key]).bumped
    added_versions = ""
    if added:
        added_versions = (
            " (with "
            + ", ".join(
                f"{name}: {sum(name in after[key].properties for key in added)}"
                for name in _VERSIONS
            )
            + ")"
        )
    lines.append(
        f"  overrides: added {len(added)}{added_versions}, "
        f"removed {len(old_overrides - new_overrides)}, changed {changed_overrides} of {len(kept)}"
    )
    lines.append(f"  changed events without a SEQUENCE or LAST-MODIFIED bump: {unbumped}")
    return lines


@dataclass
class _Bump:
    sequence: str
    last_modified: str

    @property
    def bumped(self) -> bool:
        return self.sequence in ("raised", "added") or self.last_modified in ("later", "added")

    def describe(self) -> str:
        return f"SEQUENCE {self.sequence}, LAST-MODIFIED {self.last_modified}"


def _bump(old: _Component, new: _Component) -> _Bump:
    return _Bump(
        _relation(_text(old, "SEQUENCE"), _text(new, "SEQUENCE"), _sequence, "raised", "lowered"),
        _relation(
            _text(old, "LAST-MODIFIED"), _text(new, "LAST-MODIFIED"), str, "later", "earlier"
        ),
    )


def _relation(
    old: str | None,
    new: str | None,
    key: Callable[[str], object],
    up: str,
    down: str,
) -> str:
    """How a version property moved, in words: never its values."""
    if old is None and new is None:
        return "absent"
    if old is None:
        return "added"
    if new is None:
        return "dropped"
    if old == new:
        return "unchanged"
    try:
        return up if key(new) > key(old) else down  # type: ignore[operator]
    except (TypeError, ValueError):
        return "changed"


def _sequence(value: str) -> int:
    return int(value)


def _by_occurrence(events: list[_Component]) -> dict[str | None, _Component]:
    return {_text(event, "RECURRENCE-ID"): event for event in events}


def _text(event: _Component, name: str) -> str | None:
    prop = event.first(name)
    return None if prop is None else prop.value.strip()


def _event_content(event: _Component) -> list[tuple[str, str, str]]:
    """Everything an event says apart from DTSTAMP, which changes with every fetch."""
    return [
        (name, repr(sorted(prop.params.items())), prop.value)
        for name in sorted(event.properties)
        if name != "DTSTAMP"
        for prop in event.properties[name]
    ] + [(child.name, "", repr(_event_content(child))) for child in event.children]


def _content(events: list[_Component]) -> list[list[tuple[str, str, str]]]:
    return sorted(_event_content(event) for event in events)


def _version(events: list[_Component]) -> list[tuple[str, str, str]]:
    return sorted(
        (
            _text(e, "RECURRENCE-ID") or "",
            _text(e, "SEQUENCE") or "",
            _text(e, "LAST-MODIFIED") or "",
        )
        for e in events
    )


def _exdates(events: list[_Component]) -> int:
    """How many dates the events' EXDATE properties exclude."""
    return sum(
        len(prop.value.split(","))
        for event in events
        for prop in event.properties.get("EXDATE", [])
    )


def _events_summary(events: list[_Component]) -> str:
    return f"{len(events)} ({_shape(events)[0]}), EXDATEs: {_exdates(events)}, with " + ", ".join(
        f"{name}: {sum(name in e.properties for e in events)}" for name in _VERSIONS
    )


def _changed(changed: bool) -> str:
    return "changed" if changed else "unchanged"


def _inspected(raw: bytes) -> list[str]:
    """`pa inspect`'s report of a resource's structure, indented under the test event."""
    return [f"    {line}" for line in inspect_structure(raw, "calendar").splitlines()]


if __name__ == "__main__":
    sys.exit(main())
