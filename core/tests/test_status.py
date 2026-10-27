"""The status file: written whole, read back as written."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pa_core.envelope import Source
from pa_core.status import SourceStatus, Status, read_status, write_status

AT = datetime(2026, 10, 20, 13, 15, tzinfo=UTC)


def test_sources_are_keyed_by_source_and_saved_by_name(tmp_path: Path):
    path = tmp_path / "status.json"
    gmail = SourceStatus(watermark=AT, cadence=timedelta(minutes=15))

    write_status(path, Status(sources={Source.GMAIL: gmail}), at=AT)

    assert list(json.loads(path.read_text())["sources"]) == ["gmail"]
    [key] = read_status(path).sources
    assert key is Source.GMAIL
