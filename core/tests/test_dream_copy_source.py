"""What a copy is a copy of, where no replay's run log says: a cut's wiki, or a wiki no
librarian built, dreamed over a corpus as of a time given for it (`stated_source`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pa_core.librarian.dream_copy import CopySource, DreamCopyError, stated_source
from pa_core.librarian.replay import (
    DreamingLogEntry,
    RunLogEntry,
    append_to_run_log,
)

AS_OF = datetime(2026, 10, 15, 1, tzinfo=UTC)
BUILT_BY = "0123456789ab"


def dreaming_run(**changes) -> DreamingLogEntry:
    """A committed dreaming run of a copy of a wiki no librarian built, as of `AS_OF`."""
    run = DreamingLogEntry(
        run="dreaming",
        status="committed",
        commit="c3" * 20,
        as_of=AS_OF,
        librarian_version=None,
        dreaming_version="ba9876543210",
        model="opus",
        effort="high",
        sessions=(),
        cost_usd=0.9,
        candidates=1,
        outcomes={"applied": 1, "proposed": 0, "dismissed": 0},
        lint_warnings=0,
        error=None,
    )
    return run.model_copy(update=changes)


def replay_run() -> RunLogEntry:
    return RunLogEntry(
        status="committed",
        commit="a1" * 20,
        occurred_from=AS_OF - timedelta(days=7),
        occurred_to=AS_OF,
        episodes=1,
        skipped=0,
        librarian_version=BUILT_BY,
        model="opus",
        effort="high",
        sessions=(),
        cost_usd=1.0,
        error=None,
    )


def test_a_wiki_no_librarian_built_is_dreamed_as_of_the_time_given_for_it(tmp_path: Path):
    source = stated_source(tmp_path / "run-log.jsonl", as_of=AS_OF)

    assert source == CopySource(as_of=AS_OF, librarian_version=None, cursor=None)


def test_a_copy_that_was_dreamed_as_of_that_time_is_dreamed_on(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, dreaming_run())
    append_to_run_log(run_log, dreaming_run(status="failed", commit=None))

    source = stated_source(run_log, as_of=AS_OF)

    assert source == CopySource(as_of=AS_OF, librarian_version=None, cursor=None)


def test_a_cuts_copy_is_told_the_librarian_version_and_cursor_the_cut_names(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, dreaming_run(librarian_version=BUILT_BY))

    source = stated_source(run_log, as_of=AS_OF, librarian_version=BUILT_BY, cursor="c7")

    assert source == CopySource(as_of=AS_OF, librarian_version=BUILT_BY, cursor="c7")


def test_a_copy_of_a_replay_target_isnt_dreamed_as_of_a_time_stated_for_it(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, replay_run())

    with pytest.raises(DreamCopyError, match="has a replay's runs"):
        stated_source(run_log, as_of=AS_OF)


def test_a_copy_dreamed_as_of_another_time_is_another_wikis(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, dreaming_run(as_of=AS_OF - timedelta(days=7)))

    with pytest.raises(DreamCopyError) as refused:
        stated_source(run_log, as_of=AS_OF)

    assert "has a dreaming run as of 2026-10-08T01:00:00+00:00" in str(refused.value)
    assert "this one would be as of 2026-10-15T01:00:00+00:00" in str(refused.value)


def test_a_copy_of_a_wiki_a_librarian_built_isnt_dreamed_as_one_none_did(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, dreaming_run(librarian_version=BUILT_BY))

    with pytest.raises(DreamCopyError) as refused:
        stated_source(run_log, as_of=AS_OF)

    assert f"of a wiki built by librarian version {BUILT_BY}, not one that none built" in str(
        refused.value
    )


def test_a_copy_of_a_wiki_no_librarian_built_isnt_dreamed_as_a_cuts(tmp_path: Path):
    run_log = tmp_path / "run-log.jsonl"
    append_to_run_log(run_log, dreaming_run())

    with pytest.raises(DreamCopyError) as refused:
        stated_source(run_log, as_of=AS_OF, librarian_version=BUILT_BY)

    assert f"of a wiki no librarian version built, not {BUILT_BY}" in str(refused.value)
