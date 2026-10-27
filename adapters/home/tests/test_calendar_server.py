"""CalDavServer's requests, against a stand-in that answers the way iCloud does."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from urllib.parse import urljoin, urlsplit

from pa_home.collectors.calendar_server import CalDavServer, Collection

CALENDAR = "https://p01-caldav.example.com/111/calendars/home/"
EVENTS = {
    "/111/calendars/home/one.ics": ('"e1"', "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"),
    "/111/calendars/home/two.ics": ('"e2"', "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"),
}


def multistatus(*responses: str) -> ElementTree.Element:
    body = "".join(responses)
    return ElementTree.fromstring(
        f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{body}'
        "</d:multistatus>"
    )


class ICloudLike(CalDavServer):
    """Answers PROPFIND and calendar-multiget as iCloud does. A multiget href must be the
    resource's path: iCloud answers a full URL with 404, as it did for the #85 spike."""

    def __init__(self) -> None:
        super().__init__("someone", "secret")

    def _request(self, what, method, url, body, depth):
        if method == "PROPFIND" and "sync-token" in body:
            return multistatus(
                f"<d:response><d:href>{urlsplit(CALENDAR).path}</d:href><d:propstat><d:prop>"
                "<d:sync-token>tok</d:sync-token></d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            )
        if method == "PROPFIND":
            members = "".join(
                f"<d:response><d:href>{path}</d:href>"
                f"<d:propstat><d:prop><d:getetag>{etag}</d:getetag></d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
                "<d:propstat><d:prop><d:resourcetype/></d:prop>"
                "<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>"
                for path, (etag, _) in EVENTS.items()
            )
            return multistatus(
                f"<d:response><d:href>{urlsplit(CALENDAR).path}</d:href><d:propstat><d:prop>"
                "<d:resourcetype><d:collection/></d:resourcetype></d:prop>"
                "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" + members
            )
        assert method == "REPORT" and "calendar-multiget" in body
        answers = []
        for href in re.findall(r"<d:href>(.*?)</d:href>", body):
            if href in EVENTS:
                etag, data = EVENTS[href]
                answers.append(
                    f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
                    f"<d:getetag>{etag}</d:getetag><c:calendar-data>{data}</c:calendar-data>"
                    "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                )
            else:
                answers.append(
                    f"<d:response><d:href>{href}</d:href>"
                    "<d:status>HTTP/1.1 404 Not Found</d:status></d:response>"
                )
        return multistatus(*answers)


def test_a_listing_fetches_every_resource_asking_for_each_by_its_path():
    listing = ICloudLike().listing(Collection(url=CALENDAR, name="Home"))

    assert sorted(resource.href for resource in listing.resources) == sorted(
        urljoin(CALENDAR, path) for path in EVENTS
    )
    assert {resource.etag for resource in listing.resources} == {'"e1"', '"e2"'}
    assert listing.sync_token == "tok"
    assert listing.etag_mismatches == 0
