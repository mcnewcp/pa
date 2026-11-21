"""The status file: written whole, read back as written."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pa_core.envelope import Source
from pa_core.status import (
    AssistantLogin,
    DreamingRun,
    NightlyStatus,
    SourceStatus,
    Status,
    WikiStatus,
    read_status,
    write_status,
)

AT = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)


def test_sources_are_keyed_by_source_and_saved_by_name(tmp_path: Path):
    path = tmp_path / "status.json"
    gmail = SourceStatus(watermark=AT, cadence=timedelta(minutes=15))

    write_status(path, Status(sources={Source.GMAIL: gmail}), at=AT)

    assert list(json.loads(path.read_text())["sources"]) == ["gmail"]
    [key] = read_status(path).sources
    assert key is Source.GMAIL


def test_the_last_dreaming_run_is_saved_under_the_names_the_assistant_reads(tmp_path: Path):
    path = tmp_path / "status.json"
    run = DreamingRun(
        at=AT,
        succeeded=True,
        cost_usd=2.5,
        candidates=3,
        outcomes={"applied": 1, "proposed": 1, "dismissed": 1},
        held_back=2,
    )

    write_status(path, Status(wiki=WikiStatus(last_dreaming=run, open_proposals=4)), at=AT)

    wiki = json.loads(path.read_text())["wiki"]
    assert wiki["open_proposals"] == 4
    assert wiki["last_dreaming"] == {
        "at": "2026-10-20T13:15:00Z",
        "succeeded": True,
        "reason": None,
        "cost_usd": 2.5,
        "candidates": 3,
        "outcomes": {"applied": 1, "proposed": 1, "dismissed": 1},
        "held_back": 2,
    }
    assert read_status(path).wiki.last_dreaming == run


def test_a_status_written_before_dreaming_was_recorded_still_loads(tmp_path: Path):
    path = tmp_path / "status.json"
    last_run = {"at": "2026-10-20T08:00:00Z", "succeeded": True, "cost_usd": 0.88, "warnings": 8}
    wiki = {"current_through": "2026-10-20T08:00:00Z", "last_run": last_run}
    path.write_text(json.dumps({"version": 1, "wiki": wiki}))

    status = read_status(path)

    assert status.wiki.last_run is not None and status.wiki.last_run.warnings == 8
    assert status.wiki.last_dreaming is None and status.wiki.open_proposals is None


NOW = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)
CADENCE = timedelta(minutes=15)


def a_status(behind: timedelta | None, *, nightly_since: datetime | None = None) -> Status:
    """Gmail's watermark `behind` now (None: never collected), and the nightly job, if it's
    running, started at `nightly_since`."""
    watermark = None if behind is None else NOW - behind
    gmail = SourceStatus(watermark=watermark, cadence=CADENCE)
    return Status(sources={Source.GMAIL: gmail}, nightly=NightlyStatus(running_since=nightly_since))


def test_a_source_is_stale_once_its_watermark_is_more_than_two_cadences_behind():
    assert not a_status(timedelta(minutes=29)).stale(Source.GMAIL, now=NOW)
    assert not a_status(timedelta(minutes=30)).stale(Source.GMAIL, now=NOW)
    assert a_status(timedelta(minutes=31)).stale(Source.GMAIL, now=NOW)


def test_a_source_never_collected_is_stale():
    assert a_status(None).stale(Source.GMAIL, now=NOW)


def test_no_source_is_stale_while_the_nightly_job_runs():
    running = a_status(timedelta(hours=2), nightly_since=NOW - timedelta(hours=1))

    assert not running.stale(Source.GMAIL, now=NOW)


def test_a_night_whose_three_sessions_each_run_to_their_hours_timeout_holds_staleness_off():
    # The librarian run's two sessions and the dreaming run's one, then the backup.
    long_night = a_status(timedelta(hours=4), nightly_since=NOW - timedelta(hours=3, minutes=30))

    assert not long_night.stale(Source.GMAIL, now=NOW)


def test_a_nightly_job_running_longer_than_four_hours_no_longer_holds_staleness_off():
    stuck = a_status(timedelta(hours=5), nightly_since=NOW - timedelta(hours=4, minutes=1))

    assert stuck.stale(Source.GMAIL, now=NOW)


def a_login(ends_in: timedelta | None) -> Status:
    """The assistant's login checked now, ending `ends_in` from now (None: logged out)."""
    ends = None if ends_in is None else NOW + ends_in
    return Status(assistant_login=AssistantLogin(checked_at=NOW, expires_at=ends))


def test_the_assistants_login_is_ending_within_a_week_of_its_end():
    assert not a_login(timedelta(days=7, minutes=1)).login_ending(now=NOW)
    assert a_login(timedelta(days=7)).login_ending(now=NOW)
    assert a_login(timedelta(days=-1)).login_ending(now=NOW)


def test_a_logged_out_assistant_is_ending_and_one_never_checked_isnt():
    assert a_login(None).login_ending(now=NOW)
    assert not Status().login_ending(now=NOW)
