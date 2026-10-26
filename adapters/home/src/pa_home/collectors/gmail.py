"""The Gmail collector: the owner's mail, through a Gmail client, as Takeout would export it.

Each message lands as its raw RFC 822 bytes, attachments included, behind an `X-Gmail-Labels`
header holding its labels in Takeout's names (`Inbox`, `Sent`, `Category Promotions`, and each
user label by its name). Episode selection and the forged-mail check read those names, so they
work on collected mail as they do on a Takeout export. Spam, trash, and drafts are never
captured. Mail is never reconciled: what the owner deletes or archives in Gmail stays in L0.

A backfill lists the window and records Gmail's history position first; each collection then
reads the history from that position. A position Gmail no longer keeps leads to a listing of
the time since the last collection, whose duplicates ingest collapses.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import AwareDatetime, BaseModel

from pa_core.envelope import Source
from pa_core.selection import Category
from pa_home.collectors import CollectionError, SourceInbox, read_state, write_state

_STATE = "gmail.json"
LABELS_HEADER = b"X-Gmail-Labels"

EXCLUDED = frozenset({"SPAM", "TRASH", "DRAFT"})
"""The system labels whose mail is never captured."""

RELISTING_OVERLAP = timedelta(days=1)
"""How far before the last collection a relisting reaches back, so mail that took a while to
arrive isn't missed; what was captured already collapses on ingest."""

TAKEOUT_NAMES = {
    "INBOX": "Inbox",
    "SENT": "Sent",
    "IMPORTANT": "Important",
    "STARRED": "Starred",
    "UNREAD": "Unread",
    "CHAT": "Chat",
    "CATEGORY_PERSONAL": "Category Personal",
    "CATEGORY_PROMOTIONS": "Category Promotions",
    "CATEGORY_SOCIAL": "Category Social",
    "CATEGORY_UPDATES": "Category Updates",
    "CATEGORY_FORUMS": "Category Forums",
}
"""Gmail's system label ids, by the names a Takeout export gives them, in the order written."""

# Gmail's message ids are hex; anything else isn't trusted as a payload name.
_MESSAGE_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")


class HistoryExpired(Exception):
    """Gmail no longer keeps the history from the position asked for."""


@dataclass(frozen=True)
class GmailMessage:
    raw: bytes
    """The message as Gmail holds it: RFC 822 bytes, attachments included."""
    label_ids: frozenset[str]
    """Its labels, by Gmail's ids: system labels by name (`INBOX`), user labels as `Label_<n>`."""


class GmailClient(Protocol):
    """One Gmail account, read-only."""

    def history_position(self) -> str:
        """The mailbox's current history position."""
        ...

    def changed_since(self, position: str) -> Sequence[str]:
        """The ids of messages added (or brought back from spam, trash, or drafts) since
        `position`. Raises HistoryExpired when Gmail no longer keeps that history."""
        ...

    def ids_between(self, start: datetime, end: datetime) -> Sequence[str]:
        """The ids of messages received from `start` up to `end`, spam and trash left out."""
        ...

    def fetch(self, message_id: str) -> GmailMessage:
        """The raw message, with its label ids."""
        ...

    def label_ids(self, message_id: str) -> frozenset[str]:
        """The message's label ids alone, without fetching the message."""
        ...

    def label_names(self) -> Mapping[str, str]:
        """Each user label's name, by its id."""
        ...


class GmailState(BaseModel):
    position: str | None = None
    """Gmail's history position when the last collection (or the backfill) started."""
    collected_at: AwareDatetime | None = None
    """When the last collection (or the backfill) started."""


class GmailCollector:
    """Collects the mailbox `client` reads."""

    source = Source.GMAIL

    def __init__(self, client: GmailClient) -> None:
        self.client = client
        self._label_names: Mapping[str, str] | None = None

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        saved = read_state(state / _STATE, GmailState)
        if saved.position is None or saved.collected_at is None:
            raise CollectionError("Gmail has no history position yet: run a backfill first")
        started = now()
        position = self.client.history_position()
        try:
            changed = self.client.changed_since(saved.position)
        except HistoryExpired:
            changed = self.client.ids_between(saved.collected_at - RELISTING_OVERLAP, started)
        self._capture(inbox, changed)
        write_state(state / _STATE, GmailState(position=position, collected_at=started))

    def backfill(
        self,
        inbox: SourceInbox,
        state: Path,
        *,
        window_start: datetime,
        now: Callable[[], datetime],
    ) -> None:
        started = now()
        # Taken before listing, so mail that arrives during the listing is in the next
        # collection's history.
        position = self.client.history_position()
        self._capture(inbox, self.client.ids_between(window_start, started))
        write_state(state / _STATE, GmailState(position=position, collected_at=started))

    def _capture(self, inbox: SourceInbox, message_ids: Iterable[str]) -> None:
        self._label_names = None  # The owner may have renamed a label since the last time.
        for message_id in dict.fromkeys(message_ids):
            if not _MESSAGE_ID.fullmatch(message_id):
                raise CollectionError("Gmail gave a message id that isn't a plain id")
            message = self.client.fetch(message_id)
            if message.label_ids & EXCLUDED:
                continue
            inbox.write(message_id, self._payload(message))

    def _payload(self, message: GmailMessage) -> bytes:
        """The message behind its labels, as Takeout writes it. The header is always written,
        first, so a sender's own `X-Gmail-Labels` header is never the one read."""
        names = [name for i, name in TAKEOUT_NAMES.items() if i in message.label_ids]
        users = self._users()
        # A user label's name on one line, whatever it holds.
        names += sorted(" ".join(users[i].split()) for i in message.label_ids if i in users)
        first_line = message.raw.split(b"\n", 1)[0]
        newline = b"\r\n" if first_line.endswith(b"\r") else b"\n"
        header = LABELS_HEADER + b": " + ",".join(names).encode() + newline
        return header + message.raw

    def _users(self) -> Mapping[str, str]:
        if self._label_names is None:
            self._label_names = self.client.label_names()
        return self._label_names


_CATEGORIES = {
    "CATEGORY_PROMOTIONS": Category.PROMOTIONS,
    "CATEGORY_SOCIAL": Category.SOCIAL,
    "CATEGORY_UPDATES": Category.UPDATES,
    "CATEGORY_FORUMS": Category.FORUMS,
}


def count_by_category(client: GmailClient, start: datetime, end: datetime) -> Counter[Category]:
    """How many messages received from `start` up to `end` a backfill would capture, by Gmail
    category, from their labels alone: no message is fetched. Every category is counted, from
    zero; mail with no category (or Personal) is Primary, as episode selection reads it."""
    counts = Counter(dict.fromkeys(Category, 0))
    for message_id in dict.fromkeys(client.ids_between(start, end)):
        labels = client.label_ids(message_id)
        if labels & EXCLUDED:
            continue
        found = [category for i, category in _CATEGORIES.items() if i in labels]
        counts[found[0] if found else Category.PRIMARY] += 1
    return counts
