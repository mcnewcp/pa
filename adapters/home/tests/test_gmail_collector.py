"""The Gmail collector, through the capture cycle with a fake Gmail client.

Tests assert on what a cycle leaves behind (L0, the inbox, the status file) and on what the
librarian would be offered, never on the collector's internals. The fake scripts a mailbox:
history positions, labels, spam, trash and drafts, and an expired history position.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.envelope import Envelope, Source
from pa_core.librarian.batch import production_batch
from pa_core.librarian.identity import identity_table
from pa_core.owner import Owner
from pa_core.status import read_status
from pa_home import cli
from pa_home.capture import CycleOutcome, capture_cycle
from pa_home.cli import main
from pa_home.collectors.gmail import GmailCollector, GmailMessage, HistoryExpired
from pa_home.collectors.vault import VaultCollector
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0

CHICAGO = ZoneInfo("America/Chicago")
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
CADENCES = {Source.GMAIL: timedelta(minutes=15), Source.OBSIDIAN: timedelta(minutes=15)}
START = datetime(2026, 10, 6, 9, 0, tzinfo=CHICAGO)


@dataclass
class Mail:
    raw: bytes
    labels: frozenset[str]
    received: datetime
    position: int


class FakeGmail:
    """A scripted mailbox. Its history keeps the positions after `oldest_position` only."""

    def __init__(self) -> None:
        self.mail: dict[str, Mail] = {}
        self.position = 1000
        self.oldest_position = 0
        self.user_labels = {"Label_7": "Kids/School"}
        self.fetched: list[str] = []
        self.down = False

    def deliver(
        self,
        message_id: str,
        raw: bytes,
        labels: Iterable[str],
        received: datetime = START - timedelta(hours=1),
    ) -> None:
        self.position += 1
        self.mail[message_id] = Mail(raw, frozenset(labels), received, self.position)

    # The GmailClient interface.

    def history_position(self) -> str:
        self._check()
        return str(self.position)

    def changed_since(self, position: str) -> Sequence[str]:
        self._check()
        if int(position) < self.oldest_position:
            raise HistoryExpired
        return [i for i, m in self.mail.items() if m.position > int(position)]

    def ids_between(self, start: datetime, end: datetime) -> Sequence[str]:
        # As Gmail lists messages: spam and trash left out, drafts included.
        self._check()
        return [
            i
            for i, m in self.mail.items()
            if start <= m.received < end and not m.labels & {"SPAM", "TRASH"}
        ]

    def fetch(self, message_id: str) -> GmailMessage:
        self._check()
        self.fetched.append(message_id)
        mail = self.mail[message_id]
        return GmailMessage(raw=mail.raw, label_ids=mail.labels)

    def label_ids(self, message_id: str) -> frozenset[str]:
        self._check()
        return self.mail[message_id].labels

    def label_names(self) -> dict[str, str]:
        self._check()
        return dict(self.user_labels)

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("gmail.googleapis.com: connection reset")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def email(
    message_id: str,
    *,
    sender: str = "Jay Okafor <jay@example.net>",
    to: str = "Argus McNevans <argus@example.com>",
    subject: str = "Swim lessons",
    sent: datetime = START - timedelta(hours=1),
    extra_headers: str = "",
) -> bytes:
    return (
        f"{extra_headers}"
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {format_datetime(sent)}\r\n"
        f"Message-ID: <{message_id}@mail.example.net>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "See you Tuesday.\r\n"
    ).encode()


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


@pytest.fixture
def gmail() -> FakeGmail:
    return FakeGmail()


def run_cycle(
    data_root: DataRoot,
    gmail: FakeGmail,
    clock: Clock,
    *,
    window_start: datetime | None = None,
) -> CycleOutcome:
    return capture_cycle(
        data_root,
        collectors=[GmailCollector(gmail)],
        owner=OWNER,
        cadences=CADENCES,
        now=clock,
        window_start=window_start,
    )


def backfill(data_root: DataRoot, gmail: FakeGmail, clock: Clock) -> CycleOutcome:
    return run_cycle(data_root, gmail, clock, window_start=clock.now - timedelta(days=60))


def mail_in_l0(data_root: DataRoot) -> dict[str, Envelope]:
    return {
        envelope.native_id.split("@")[0]: envelope
        for envelope in FilesystemL0(data_root.l0).envelopes()
        if envelope.source == Source.GMAIL
    }


def files_under(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def test_messages_land_with_their_labels_in_takeouts_names(data_root: DataRoot, gmail: FakeGmail):
    gmail.deliver("m1", email("m1"), ["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"])
    gmail.deliver("m2", email("m2"), ["INBOX", "CATEGORY_UPDATES", "Label_7"])
    gmail.deliver("m3", email("m3", sender="Argus <argus@example.com>"), ["SENT"])

    backfill(data_root, gmail, Clock(START))

    landed = mail_in_l0(data_root)
    assert landed["m1"].labels == ["Inbox", "Important", "Category Personal"]
    assert landed["m2"].labels == ["Inbox", "Category Updates", "Kids/School"]
    assert landed["m3"].labels == ["Sent"]
    assert files_under(data_root.inbox) == []


def test_a_collection_collects_only_the_mail_new_since_the_last(
    data_root: DataRoot, gmail: FakeGmail
):
    gmail.deliver("m1", email("m1"), ["INBOX"])
    clock = Clock(START)
    backfill(data_root, gmail, clock)

    gmail.deliver("m2", email("m2"), ["INBOX"], received=START + timedelta(minutes=5))
    clock.now = START + timedelta(minutes=15)
    gmail.fetched.clear()
    outcome = run_cycle(data_root, gmail, clock)

    assert outcome.collected == {Source.GMAIL: 1}
    assert gmail.fetched == ["m2"]
    assert sorted(mail_in_l0(data_root)) == ["m1", "m2"]
    assert read_status(data_root.status).sources[Source.GMAIL].watermark == clock.now


def test_spam_trash_and_drafts_never_land(data_root: DataRoot, gmail: FakeGmail):
    gmail.deliver("spam1", email("spam1"), ["SPAM", "CATEGORY_PROMOTIONS"])
    gmail.deliver("trash1", email("trash1"), ["TRASH", "INBOX"])
    gmail.deliver("draft1", email("draft1", sender="Argus <argus@example.com>"), ["DRAFT"])
    gmail.deliver("kept1", email("kept1"), ["INBOX"])
    clock = Clock(START)
    backfill(data_root, gmail, clock)

    later = START + timedelta(minutes=5)
    gmail.deliver("spam2", email("spam2"), ["SPAM"], received=later)
    gmail.deliver("trash2", email("trash2"), ["TRASH"], received=later)
    gmail.deliver("draft2", email("draft2"), ["DRAFT"], received=later)
    gmail.deliver("kept2", email("kept2"), ["SENT"], received=later)
    clock.now = START + timedelta(minutes=15)
    run_cycle(data_root, gmail, clock)

    assert sorted(mail_in_l0(data_root)) == ["kept1", "kept2"]
    assert files_under(data_root.inbox) == []
    assert read_status(data_root.status).sources[Source.GMAIL].quarantined == 0


def test_promotions_and_social_land_but_are_never_offered_to_the_librarian(
    data_root: DataRoot, gmail: FakeGmail
):
    gmail.deliver("primary", email("primary"), ["INBOX", "CATEGORY_PERSONAL"])
    gmail.deliver("promo", email("promo"), ["INBOX", "CATEGORY_PROMOTIONS"])
    gmail.deliver("social", email("social"), ["INBOX", "CATEGORY_SOCIAL"])
    gmail.deliver("update", email("update"), ["INBOX", "CATEGORY_UPDATES"])
    gmail.deliver("forum", email("forum"), ["CATEGORY_FORUMS"])
    backfill(data_root, gmail, Clock(START))

    landed = mail_in_l0(data_root)
    assert sorted(landed) == ["forum", "primary", "promo", "social", "update"]
    batch = production_batch(FilesystemL0(data_root.l0), after=None, time_zone=CHICAGO)
    assert batch is not None
    offered = {episode.episode_id: episode for episode in batch.episodes}
    assert set(offered) == {landed[m].episode_id for m in ("primary", "update", "forum")}
    assert offered[landed["update"].episode_id].judge_relevance
    assert not offered[landed["primary"].episode_id].judge_relevance


def test_the_owners_sent_mail_passes_the_forged_mail_check(data_root: DataRoot, gmail: FakeGmail):
    owner = "Argus McNevans <argus@example.com>"
    gmail.deliver("sent", email("sent", sender=owner, to="Jay <jay@example.net>"), ["SENT"])
    # Mail from outside that claims the owner's address, and labels itself as sent.
    forged = email("forged", sender=owner, extra_headers="X-Gmail-Labels: Sent\r\n")
    gmail.deliver("forged", forged, ["INBOX", "CATEGORY_PERSONAL"])
    backfill(data_root, gmail, Clock(START))

    landed = mail_in_l0(data_root)
    table = identity_table(landed.values(), OWNER)
    assert [mail.episode_id for mail in table.forged] == [landed["forged"].episode_id]


def test_an_expired_position_leads_to_a_relisting_whose_duplicates_collapse(
    data_root: DataRoot, gmail: FakeGmail
):
    gmail.deliver("old", email("old"), ["INBOX"], received=START - timedelta(days=30))
    gmail.deliver("m1", email("m1"), ["INBOX"])
    clock = Clock(START)
    backfill(data_root, gmail, clock)

    # The devbox was off for a fortnight: Gmail no longer keeps the history from then.
    clock.now = START + timedelta(days=14)
    gmail.deliver("m2", email("m2"), ["INBOX"], received=clock.now - timedelta(days=2))
    gmail.oldest_position = gmail.position
    gmail.fetched.clear()
    outcome = run_cycle(data_root, gmail, clock)

    assert outcome.failures == {}
    assert "m2" in gmail.fetched
    assert "old" not in gmail.fetched
    assert sorted(mail_in_l0(data_root)) == ["m1", "m2", "old"]
    assert outcome.ingested is not None
    assert outcome.ingested.ingested == 1
    assert outcome.ingested.unchanged == len(gmail.fetched) - 1
    assert files_under(data_root.inbox) == []

    # The position is good again from here on.
    clock.now += timedelta(minutes=15)
    gmail.deliver("m3", email("m3"), ["INBOX"], received=clock.now - timedelta(minutes=1))
    gmail.fetched.clear()
    run_cycle(data_root, gmail, clock)
    assert gmail.fetched == ["m3"]


def test_a_backfill_stays_inside_its_window(data_root: DataRoot, gmail: FakeGmail):
    window_start = START - timedelta(days=60)
    gmail.deliver("before", email("before"), ["INBOX"], received=window_start - timedelta(hours=1))
    gmail.deliver("first", email("first"), ["INBOX"], received=window_start)
    gmail.deliver("recent", email("recent"), ["INBOX"])
    clock = Clock(START)

    outcome = run_cycle(data_root, gmail, clock, window_start=window_start)

    assert outcome.collected == {Source.GMAIL: 2}
    assert sorted(mail_in_l0(data_root)) == ["first", "recent"]
    assert "before" not in gmail.fetched
    # Collections carry on from the backfill, without reaching back before its window.
    clock.now += timedelta(minutes=15)
    outcome = run_cycle(data_root, gmail, clock)
    assert outcome.collected == {Source.GMAIL: 0}
    assert "before" not in mail_in_l0(data_root)


def test_a_client_failure_leaves_the_watermark_stale_while_the_vault_carries_on(
    data_root: DataRoot, gmail: FakeGmail, tmp_path: Path
):
    vault = tmp_path / "vault"
    (vault / "Daily").mkdir(parents=True)
    (vault / "Daily" / "2026-10-05.md").write_text("## Swim lessons\nSigned up for Tuesdays.\n")
    gmail.deliver("m1", email("m1", subject="Dinner at Bellwether"), ["INBOX"])
    collectors = [GmailCollector(gmail), VaultCollector(vault, "Daily", CHICAGO)]
    backfill(data_root, gmail, Clock(START))

    gmail.down = True
    later = START + timedelta(minutes=15)
    outcome = capture_cycle(
        data_root, collectors=collectors, owner=OWNER, cadences=CADENCES, now=Clock(later)
    )

    assert set(outcome.failures) == {Source.GMAIL}
    status = read_status(data_root.status)
    assert status.sources[Source.GMAIL].watermark == START
    failure = status.sources[Source.GMAIL].last_failure
    assert failure is not None
    assert failure.at == later
    assert status.sources[Source.OBSIDIAN].watermark == later
    assert status.ingest.last_success == later
    assert "Bellwether" not in data_root.status.read_text()


def test_a_collection_before_any_backfill_fails_and_says_what_to_do(
    data_root: DataRoot, gmail: FakeGmail
):
    gmail.deliver("m1", email("m1"), ["INBOX"])

    outcome = run_cycle(data_root, gmail, Clock(START))

    assert outcome.failures == {
        Source.GMAIL: "Gmail has no history position yet: run a backfill first"
    }
    assert mail_in_l0(data_root) == {}
    assert read_status(data_root.status).sources[Source.GMAIL].watermark is None


# `pa capture` and `pa gmail`, configured as the owner would configure them.

# Distinctive text that must never reach the output, the logs, or the status file.
SALT = "Quillon Vasquez-Ferro booked the Bellwether loft"


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataRoot:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_GMAIL_TOKEN", str(tmp_path / "config" / "gmail-token.json"))
    monkeypatch.setenv("PA_GMAIL_CLIENT", str(tmp_path / "config" / "gmail-client.json"))
    return DataRoot(root)


@pytest.fixture
def fake_account(gmail: FakeGmail, monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """The fake mailbox stands in for the owner's Gmail account wherever `pa` opens it."""
    monkeypatch.setattr(cli, "open_gmail", lambda token: gmail)
    return gmail


def days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def test_the_dry_run_prints_counts_per_category_without_fetching_any_message(
    configured: DataRoot,
    fake_account: FakeGmail,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    salted = email("salted", subject=SALT)
    for message_id, labels in [
        ("p1", ["INBOX", "CATEGORY_PERSONAL"]),
        ("p2", ["SENT"]),
        ("promo", ["INBOX", "CATEGORY_PROMOTIONS"]),
        ("social", ["INBOX", "CATEGORY_SOCIAL"]),
        ("update", ["INBOX", "CATEGORY_UPDATES"]),
        ("forum", ["CATEGORY_FORUMS"]),
        ("spam", ["SPAM", "CATEGORY_PROMOTIONS"]),
        ("draft", ["DRAFT"]),
    ]:
        fake_account.deliver(message_id, salted, labels, received=days_ago(3))
    fake_account.deliver("old", salted, ["INBOX"], received=days_ago(40))
    window_start = days_ago(30).astimezone(CHICAGO).date().isoformat()

    assert main(["gmail", "dry-run", "--window-start", window_start]) == 0

    output = capsys.readouterr()
    assert fake_account.fetched == []
    assert output.out == (
        f"gmail: 6 messages since {window_start}: "
        "primary 2, promotions 1, social 1, updates 1, forums 1\n"
    )
    assert SALT not in output.out + output.err + caplog.text
    assert not configured.path.exists()


def test_pa_capture_backfills_then_collects_reporting_counts_only(
    configured: DataRoot,
    fake_account: FakeGmail,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    fake_account.deliver("m1", email("m1", subject=SALT), ["INBOX"], received=days_ago(3))
    fake_account.deliver("old", email("old", subject=SALT), ["INBOX"], received=days_ago(90))
    window_start = days_ago(60).isoformat()

    assert main(["capture", "--backfill", window_start]) == 0
    fake_account.deliver("m2", email("m2", subject=SALT), ["INBOX"], received=days_ago(0.01))
    assert main(["capture"]) == 0

    output = capsys.readouterr()
    assert sorted(mail_in_l0(configured)) == ["m1", "m2"]
    assert output.out.count("gmail: collected 1") == 2
    assert SALT not in output.out + output.err + caplog.text
    assert SALT not in configured.status.read_text()


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (None, "Gmail has no token yet: run pa gmail auth"),
        (0o644, "Gmail's token is readable by others: chmod 600 it"),
    ],
)
def test_a_missing_or_exposed_token_fails_gmail_alone(
    configured: DataRoot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    token: int | None,
    reason: str,
):
    if token is not None:
        path = tmp_path / "config" / "gmail-token.json"
        path.parent.mkdir()
        path.write_text('{"refresh_token": "not-a-real-token"}')
        path.chmod(token)
    vault = tmp_path / "vault"
    (vault / "Daily").mkdir(parents=True)
    (vault / "Daily" / "2020-01-01.md").write_text("## Errands\nPicked up the bikes.\n")
    monkeypatch.setenv("PA_VAULT_DIR", str(vault))
    monkeypatch.setenv("PA_VAULT_DAILY_NOTES", "Daily")

    assert main(["capture", "--backfill", "2019-12-01"]) == 1

    output = capsys.readouterr()
    assert f"gmail: collection failed: {reason}" in output.err
    status = read_status(configured.status)
    assert status.sources[Source.GMAIL].watermark is None
    assert status.sources[Source.OBSIDIAN].watermark is not None


def test_pa_gmail_auth_needs_the_oauth_client_from_the_cloud_project(
    configured: DataRoot, capsys: pytest.CaptureFixture[str]
):
    assert main(["gmail", "auth"]) == 1

    assert "Gmail's OAuth client isn't there" in capsys.readouterr().err
