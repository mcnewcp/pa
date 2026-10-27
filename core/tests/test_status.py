"""The status file: written whole, read back as written."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pa_core.envelope import Source
from pa_core.status import NightlyStatus, SourceStatus, Status, read_status, write_status

AT = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)


def test_sources_are_keyed_by_source_and_saved_by_name(tmp_path: Path):
    path = tmp_path / "status.json"
    gmail = SourceStatus(watermark=AT, cadence=timedelta(minutes=15))

    write_status(path, Status(sources={Source.GMAIL: gmail}), at=AT)

    assert list(json.loads(path.read_text())["sources"]) == ["gmail"]
    [key] = read_status(path).sources
    assert key is Source.GMAIL


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


def test_a_nightly_job_running_longer_than_three_hours_no_longer_holds_staleness_off():
    stuck = a_status(timedelta(hours=4), nightly_since=NOW - timedelta(hours=3, minutes=1))

    assert stuck.stale(Source.GMAIL, now=NOW)
