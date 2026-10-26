"""The capture cycle over a temporary data root and vault.

Tests assert on what a cycle leaves behind: the inbox, L0, the collector state, and the status
file.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.envelope import Envelope, Source
from pa_core.owner import Owner
from pa_core.status import read_status
from pa_home.capture import CycleOutcome, capture_cycle
from pa_home.collectors import SourceInbox
from pa_home.collectors.vault import VaultCollector
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.lock import data_root_lock

CHICAGO = ZoneInfo("America/Chicago")
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
CADENCES = {Source.OBSIDIAN: timedelta(minutes=15)}


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    (path / "Daily").mkdir(parents=True)
    return path


def write_note(vault: Path, day: str, text: str) -> None:
    (vault / "Daily" / f"{day}.md").write_text(text)


def run_cycle(data_root: DataRoot, vault: Path, clock: Clock) -> CycleOutcome:
    return capture_cycle(
        data_root,
        collectors=[VaultCollector(vault, "Daily", CHICAGO)],
        owner=OWNER,
        cadences=CADENCES,
        now=clock,
    )


def notes_in_l0(data_root: DataRoot) -> list[Envelope]:
    return [
        envelope
        for envelope in FilesystemL0(data_root.l0).envelopes()
        if envelope.source == Source.OBSIDIAN
    ]


def files_under(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p.relative_to(path) for p in path.rglob("*") if p.is_file())


def test_a_finished_note_lands_in_l0_captured_when_the_collector_read_it(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    # 1 a.m. on 6 October in Chicago: the 5th is over.
    clock = Clock(datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO))

    run_cycle(data_root, vault, clock)

    [note] = notes_in_l0(data_root)
    assert note.native_id == "Daily/2026-10-05.md"
    assert note.captured_at == datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
    assert "Signed up for Tuesdays." in note.body
    assert files_under(data_root.inbox) == []


def test_todays_note_is_not_captured_until_its_day_is_over(data_root: DataRoot, vault: Path):
    write_note(vault, "2026-10-06", "## Errands\nStill writing this one.\n")
    clock = Clock(datetime(2026, 10, 6, 23, 59, tzinfo=CHICAGO))

    run_cycle(data_root, vault, clock)

    assert notes_in_l0(data_root) == []

    clock.now = datetime(2026, 10, 7, 0, 5, tzinfo=CHICAGO)
    run_cycle(data_root, vault, clock)

    [note] = notes_in_l0(data_root)
    assert note.native_id == "Daily/2026-10-06.md"


def test_an_old_note_that_changed_lands_as_a_new_version(data_root: DataRoot, vault: Path):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    clock = Clock(datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO))
    run_cycle(data_root, vault, clock)

    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Thursdays, not Tuesdays.\n")
    clock.now = datetime(2026, 10, 9, 8, 30, tzinfo=CHICAGO)
    run_cycle(data_root, vault, clock)

    versions = sorted(notes_in_l0(data_root), key=lambda note: note.captured_at)
    assert [note.native_id for note in versions] == ["Daily/2026-10-05.md"] * 2
    assert [note.captured_at for note in versions] == [
        datetime(2026, 10, 6, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 13, 30, tzinfo=UTC),
    ]
    assert "Thursdays" in versions[1].body


def test_an_unchanged_note_is_not_collected_again(data_root: DataRoot, vault: Path):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    clock = Clock(datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO))
    run_cycle(data_root, vault, clock)

    clock.now += timedelta(minutes=15)
    outcome = run_cycle(data_root, vault, clock)

    assert outcome.collected == {Source.OBSIDIAN: 0}
    [note] = notes_in_l0(data_root)
    assert note.captured_at == datetime(2026, 10, 6, 6, 0, tzinfo=UTC)


def test_a_cycle_publishes_the_watermark_once_its_notes_have_landed(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    start = datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)

    run_cycle(data_root, vault, Clock(start))

    assert len(notes_in_l0(data_root)) == 1
    obsidian = read_status(data_root.status).sources[Source.OBSIDIAN]
    assert obsidian.watermark == start
    assert obsidian.cadence == timedelta(minutes=15)
    assert obsidian.last_attempt == start
    assert obsidian.last_failure is None
    assert obsidian.quarantined == 0
    # The collector's own state is under the data root, outside L0.
    assert files_under(data_root.state / "obsidian") != []


def test_a_backfill_captures_the_finished_notes_inside_its_window_and_no_earlier(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-08-05", "## Errands\nBefore the window.\n")
    write_note(vault, "2026-08-07", "## Errands\nThe window's first day.\n")
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    write_note(vault, "2026-10-06", "## Errands\nStill writing this one.\n")
    clock = Clock(datetime(2026, 10, 6, 9, 0, tzinfo=CHICAGO))
    window_start = datetime(2026, 8, 7, 0, 0, tzinfo=CHICAGO)

    capture_cycle(
        data_root,
        collectors=[VaultCollector(vault, "Daily", CHICAGO)],
        owner=OWNER,
        cadences=CADENCES,
        now=clock,
        window_start=window_start,
    )

    assert sorted(note.native_id for note in notes_in_l0(data_root)) == [
        "Daily/2026-08-07.md",
        "Daily/2026-10-05.md",
    ]
    # Collections carry on from the backfill without reaching back before its window.
    write_note(vault, "2026-08-05", "## Errands\nBefore the window, edited.\n")
    clock.now = datetime(2026, 10, 7, 1, 0, tzinfo=CHICAGO)
    run_cycle(data_root, vault, clock)

    assert sorted(note.native_id for note in notes_in_l0(data_root)) == [
        "Daily/2026-08-07.md",
        "Daily/2026-10-05.md",
        "Daily/2026-10-06.md",
    ]


class FailingCollector:
    """A collector whose source is down; its error message quotes what it was reading."""

    def __init__(self, source: Source) -> None:
        self.source = source

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        raise ValueError("could not parse 'Dinner with Jay at Bellwether'")

    def backfill(
        self,
        inbox: SourceInbox,
        state: Path,
        *,
        window_start: datetime,
        now: Callable[[], datetime],
    ) -> None:
        self.collect(inbox, state, now=now)


def test_a_failed_collection_keeps_the_old_watermark_and_records_why_without_content(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    first = datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)
    run_cycle(data_root, vault, Clock(first))

    (vault / "Daily").rename(vault / "Elsewhere")
    second = first + timedelta(minutes=15)
    outcome = run_cycle(data_root, vault, Clock(second))

    reason = "the vault copy's daily-notes folder doesn't exist"
    assert outcome.failures == {Source.OBSIDIAN: reason}
    obsidian = read_status(data_root.status).sources[Source.OBSIDIAN]
    assert obsidian.watermark == first
    assert obsidian.last_attempt == second
    assert obsidian.last_failure is not None
    assert obsidian.last_failure.at == second
    assert obsidian.last_failure.reason == reason


def test_one_failing_collector_stops_neither_the_others_nor_ingest(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    start = datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)

    capture_cycle(
        data_root,
        collectors=[FailingCollector(Source.GMAIL), VaultCollector(vault, "Daily", CHICAGO)],
        owner=OWNER,
        cadences={**CADENCES, Source.GMAIL: timedelta(minutes=15)},
        now=Clock(start),
    )

    assert len(notes_in_l0(data_root)) == 1
    status = read_status(data_root.status)
    assert status.sources[Source.OBSIDIAN].watermark == start
    gmail = status.sources[Source.GMAIL]
    assert gmail.watermark is None
    assert gmail.last_failure is not None
    assert gmail.last_failure.reason == "unexpected ValueError"
    assert "Bellwether" not in data_root.status.read_text()


def test_a_quarantined_note_is_counted_without_holding_the_watermark_back(
    data_root: DataRoot, vault: Path
):
    write_note(vault, "2026-10-04", "## Swim lessons\nSigned up for Tuesdays.\n")
    # Not UTF-8, so it can't become an episode.
    (vault / "Daily" / "2026-10-05.md").write_bytes(b"## Swim lessons\n\xff\xfe\n")
    start = datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)

    run_cycle(data_root, vault, Clock(start))

    assert [note.native_id for note in notes_in_l0(data_root)] == ["Daily/2026-10-04.md"]
    assert files_under(data_root.inbox) == []
    obsidian = read_status(data_root.status).sources[Source.OBSIDIAN]
    assert obsidian.quarantined == 1
    assert obsidian.watermark == start


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes any directory")
def test_the_watermark_waits_until_ingest_has_landed_the_notes(data_root: DataRoot, vault: Path):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")
    first = datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)
    run_cycle(data_root, vault, Clock(first))

    # L0 can't take the next note, so ingest fails and the note waits in the inbox.
    write_note(vault, "2026-11-01", "## Errands\nPicked up the bikes.\n")
    raw = data_root.l0 / "raw" / "obsidian"
    raw.chmod(0o555)
    try:
        second = datetime(2026, 11, 2, 1, 0, tzinfo=CHICAGO)
        outcome = run_cycle(data_root, vault, Clock(second))
    finally:
        raw.chmod(0o755)

    assert outcome.ingest_failure is not None
    assert len(files_under(data_root.inbox / "obsidian")) == 1
    status = read_status(data_root.status)
    assert status.sources[Source.OBSIDIAN].watermark == first
    assert status.ingest.last_failure is not None

    # The next cycle collects nothing new, lands the waiting note, and publishes.
    third = second + timedelta(minutes=15)
    outcome = run_cycle(data_root, vault, Clock(third))

    assert outcome.collected == {Source.OBSIDIAN: 0}
    assert len(notes_in_l0(data_root)) == 2
    assert files_under(data_root.inbox) == []
    assert read_status(data_root.status).sources[Source.OBSIDIAN].watermark == third


def test_a_cycle_that_finds_the_lock_held_skips(data_root: DataRoot, vault: Path):
    write_note(vault, "2026-10-05", "## Swim lessons\nSigned up for Tuesdays.\n")

    with data_root_lock(data_root):
        outcome = run_cycle(data_root, vault, Clock(datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO)))

    assert outcome.skipped
    assert files_under(data_root.inbox) == []
    assert notes_in_l0(data_root) == []
    assert not data_root.status.exists()
    assert files_under(data_root.state) == []
