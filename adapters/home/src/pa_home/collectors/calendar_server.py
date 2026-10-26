"""The calendar server: the CalDAV collections an account serves, read-only.

`CalendarServer` is what a reader of calendars needs from CalDAV: the calendar collections, a
full listing of one (every resource with its etag, plus the collection's sync token), and the
changes since a sync token (RFC 6578). `CalDavServer` is it over iCloud, by the `caldav`
library; tests give a fake.

Nothing here prints or logs, and no error it raises holds a URL, an href, a credential, or any
of a resource's content: only the request that failed and its HTTP status.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

from caldav.davclient import DAVClient
from caldav.lib import error as caldav_error

from pa_core.errors import PaError

ICLOUD_URL = "https://caldav.icloud.com/"
MULTIGET_BATCH = 100
"""How many resources one calendar-multiget asks for."""


@dataclass(frozen=True)
class Collection:
    """A calendar collection the account serves."""

    url: str = field(repr=False)
    name: str | None
    """Its display name, which the owner sees and configures; None if it has none."""
    components: tuple[str, ...] = ()
    """The component types it holds, such as VEVENT or VTODO; empty if the server didn't say."""
    kinds: tuple[str, ...] = ()
    """Its resource types other than collection and calendar, such as `shared` or
    `shared-owner` (calendarserver's sharing) or `subscribed`."""
    writable: bool | None = None
    """Whether the account may write it; None if the server didn't say."""


@dataclass(frozen=True)
class Resource:
    """One calendar resource as served: its href, its etag, and its bytes."""

    href: str = field(repr=False)
    etag: str | None = field(repr=False)
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class Listing:
    """Everything a collection holds, and its sync token taken just before it was listed."""

    sync_token: str | None = field(repr=False)
    resources: list[Resource]
    etag_mismatches: int = 0
    """Resources whose etag changed between listing them and fetching their data."""


@dataclass(frozen=True)
class Changes:
    """A sync report: what changed in a collection since a sync token."""

    sync_token: str | None = field(repr=False)
    changed: dict[str, str | None] = field(repr=False)
    """Each added or changed resource's href, with its etag if the server gave one."""
    removed: list[str] = field(repr=False)
    """Each removed resource's href."""


class CalendarServerError(PaError):
    """A request to the calendar server that failed. Its message names the request and the HTTP
    status, never a URL or anything the server sent."""

    def __init__(self, what: str, status: int | None = None, why: str | None = None) -> None:
        super().__init__(f"{what} failed ({why or (f'HTTP {status}' if status else 'no reply')})")
        self.status = status


class SyncTokenRejected(CalendarServerError):
    """The server refused a sync token, as it does once a token has expired."""

    def __init__(self, status: int | None) -> None:
        super().__init__("the sync report", status, f"the token was refused: HTTP {status}")


class CalendarServer(Protocol):
    """The calendar collections an account serves, read-only."""

    def collections(self) -> list[Collection]: ...

    def listing(self, collection: Collection) -> Listing: ...

    def changes(self, collection: Collection, sync_token: str) -> Changes:
        """What changed since `sync_token`; raises SyncTokenRejected if it's refused."""
        ...


DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"
_PLAIN_KINDS = frozenset({"collection", "calendar"})
_WRITE_PRIVILEGES = frozenset({"write", "write-content", "all"})

_PROPFIND_COLLECTIONS = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <c:supported-calendar-component-set/>
    <d:current-user-privilege-set/>
  </d:prop>
</d:propfind>"""
_PROPFIND_SYNC_TOKEN = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:sync-token/></d:prop></d:propfind>"""
_PROPFIND_MEMBERS = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:resourcetype/></d:prop></d:propfind>"""
_SYNC_COLLECTION = """<?xml version="1.0" encoding="utf-8"?>
<d:sync-collection xmlns:d="DAV:">
  <d:sync-token>{token}</d:sync-token>
  <d:sync-level>1</d:sync-level>
  <d:prop><d:getetag/></d:prop>
</d:sync-collection>"""
_MULTIGET = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
{hrefs}
</c:calendar-multiget>"""


class CalDavServer:
    """`CalendarServer` over CalDAV, by the `caldav` library: iCloud's by default.

    The library is used for what's fiddly (authentication, and finding the principal and its
    calendar home, which iCloud serves from a per-account host); the few requests the reader
    needs are made through its client, and their replies parsed here, so that a sync token
    the server refuses is reported as refused rather than quietly replaced by a full listing.
    """

    def __init__(self, username: str, password: str, url: str = ICLOUD_URL) -> None:
        self._username = username
        self._password = password
        self._url = url
        self._client: DAVClient | None = None

    def collections(self) -> list[Collection]:
        client = self._connect()
        try:
            home_set = client.principal().calendar_home_set
        except Exception as error:
            raise _failure("finding the calendar home", error) from None
        if home_set is None:
            raise CalendarServerError("finding the calendar home", why="the server names none")
        home = str(home_set.url)
        reply = self._request("listing the calendars", "PROPFIND", home, _PROPFIND_COLLECTIONS, 1)
        found: list[Collection] = []
        for href, prop in _responses(reply):
            kinds = _child_names(prop.find(f"{DAV}resourcetype"))
            if "calendar" not in kinds:
                continue
            name = prop.findtext(f"{DAV}displayname")
            components = prop.find(f"{CALDAV}supported-calendar-component-set")
            privileges = prop.find(f"{DAV}current-user-privilege-set")
            found.append(
                Collection(
                    url=urljoin(home, href),
                    name=name.strip() if name and name.strip() else None,
                    components=tuple(
                        comp.get("name", "").upper()
                        for comp in (components if components is not None else ())
                    ),
                    kinds=tuple(kind for kind in kinds if kind not in _PLAIN_KINDS),
                    writable=None
                    if privileges is None
                    else bool(_WRITE_PRIVILEGES & set(_privileges(privileges))),
                )
            )
        return found

    def listing(self, collection: Collection) -> Listing:
        reply = self._request(
            "reading the sync token", "PROPFIND", collection.url, _PROPFIND_SYNC_TOKEN, 0
        )
        token = next((prop.findtext(f"{DAV}sync-token") for _, prop in _responses(reply)), None)
        reply = self._request(
            "listing a calendar", "PROPFIND", collection.url, _PROPFIND_MEMBERS, 1
        )
        listed: dict[str, str | None] = {}
        for href, prop in _responses(reply):
            url = urljoin(collection.url, href)
            if (
                _same_url(url, collection.url)
                or prop.find(f"{DAV}resourcetype/{DAV}collection") is not None
            ):
                continue
            listed[url] = prop.findtext(f"{DAV}getetag")
        resources: list[Resource] = []
        mismatches = 0
        hrefs = list(listed)
        for start in range(0, len(hrefs), MULTIGET_BATCH):
            batch = hrefs[start : start + MULTIGET_BATCH]
            body = _MULTIGET.format(hrefs="\n".join(f"  <d:href>{_xml(h)}</d:href>" for h in batch))
            reply = self._request("fetching resources", "REPORT", collection.url, body, None)
            for href, prop in _responses(reply):
                url = urljoin(collection.url, href)
                data = prop.findtext(f"{CALDAV}calendar-data")
                if url not in listed or data is None:
                    continue
                etag = prop.findtext(f"{DAV}getetag") or listed[url]
                mismatches += etag != listed[url]
                resources.append(Resource(href=url, etag=etag, data=data.encode("utf-8")))
        return Listing(sync_token=token, resources=resources, etag_mismatches=mismatches)

    def changes(self, collection: Collection, sync_token: str) -> Changes:
        body = _SYNC_COLLECTION.format(token=_xml(sync_token))
        try:
            reply = self._request("the sync report", "REPORT", collection.url, body, 1)
        except CalendarServerError as error:
            if error.status in (400, 403, 409, 412):
                raise SyncTokenRejected(error.status) from None
            raise
        changed: dict[str, str | None] = {}
        removed: list[str] = []
        for href, status, prop in _sync_responses(reply):
            url = urljoin(collection.url, href)
            if _same_url(url, collection.url):
                continue
            if status == 404:
                removed.append(url)
            else:
                changed[url] = prop.findtext(f"{DAV}getetag") if prop is not None else None
        token = reply.findtext(f"{DAV}sync-token")
        return Changes(sync_token=token, changed=changed, removed=removed)

    def _connect(self) -> DAVClient:
        if self._client is None:
            _quiet_the_library()
            caldav_error.debug_dump_communication = False  # it would dump the Authorization header
            self._client = DAVClient(
                url=self._url,
                username=self._username,
                password=self._password,
                enable_rfc6764=False,
                timeout=60,
            )
        return self._client

    def _request(
        self, what: str, method: str, url: str, body: str, depth: int | None
    ) -> ElementTree.Element:
        """The multistatus reply to one request, or CalendarServerError naming `what`."""
        client = self._connect()
        headers = {"Content-Type": 'application/xml; charset="utf-8"'}
        if depth is not None:
            headers["Depth"] = str(depth)
        try:
            response = client.request(url, method, body, headers)
        except Exception as error:
            raise _failure(what, error) from None
        if response.status != 207:
            raise CalendarServerError(what, response.status)
        try:
            return ElementTree.fromstring(response.raw.encode("utf-8"))
        except ElementTree.ParseError:
            raise CalendarServerError(what, why="the reply isn't XML") from None


def _failure(what: str, error: Exception) -> CalendarServerError:
    """A CalendarServerError for an exception the library raised. Only its type, and the HTTP
    status an authorization error stands for, are kept: its message can hold the URL, which
    names the account."""
    if type(error).__name__ == "AuthorizationError":
        # Raised for a 401 or a 403 once authentication has been tried; its reason is the
        # HTTP reason phrase, such as Forbidden. A refused sync token is a 403.
        reason = str(getattr(error, "reason", "")).lower()
        return CalendarServerError(what, 403 if "forbidden" in reason or "403" in reason else 401)
    return CalendarServerError(what, why=type(error).__name__)


def _responses(reply: ElementTree.Element) -> Iterator[tuple[str, ElementTree.Element]]:
    """Each response's href, with the properties it found (those in a 200 propstat)."""
    for response in reply.iter(f"{DAV}response"):
        href = (response.findtext(f"{DAV}href") or "").strip()
        found = ElementTree.Element(f"{DAV}prop")
        for propstat in response.findall(f"{DAV}propstat"):
            if _code(propstat.findtext(f"{DAV}status")) in (200, None):
                found.extend(propstat.findall(f"{DAV}prop/*"))
        yield href, found


def _sync_responses(
    reply: ElementTree.Element,
) -> Iterator[tuple[str, int | None, ElementTree.Element | None]]:
    """Each sync-report response's href, its own status (404 for a removed member), and the
    properties it found."""
    for response in reply.iter(f"{DAV}response"):
        href = (response.findtext(f"{DAV}href") or "").strip()
        status = _code(response.findtext(f"{DAV}status"))
        found = None
        for propstat in response.findall(f"{DAV}propstat"):
            if _code(propstat.findtext(f"{DAV}status")) in (200, None):
                found = propstat.find(f"{DAV}prop")
        yield href, status, found


def _code(status: str | None) -> int | None:
    """The code in an `HTTP/1.1 404 Not Found` status line."""
    parts = (status or "").split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def _child_names(element: ElementTree.Element | None) -> list[str]:
    return [] if element is None else [_local(child.tag) for child in element]


def _privileges(element: ElementTree.Element) -> Iterable[str]:
    for privilege in element.iter(f"{DAV}privilege"):
        yield from _child_names(privilege)


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _same_url(one: str, other: str) -> bool:
    return one.rstrip("/") == other.rstrip("/")


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quiet_the_library() -> None:
    """Silence the CalDAV and HTTP libraries' logs: their messages hold URLs, which name the
    account, and replies, which hold content."""
    for name in ("caldav", "niquests", "urllib3", "urllib3_future"):
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.propagate = False
