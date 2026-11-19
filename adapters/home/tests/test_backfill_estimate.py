"""The backfill's counts and replay estimate (story 87 of #80): `pa backfill-estimate`, and the
same lines at the end of `pa capture --backfill`, over a temporary data root. Numbers only: no
subject, name or address is printed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pa_home.cli import main

WINDOW_START = "2026-10-01"
# Distinctive text that must never reach the output.
SALT = "Quillon Vasquez-Ferro"


def mail(number: int, date: str, *labels: str) -> bytes:
    return (
        f"X-Gmail-Labels: {','.join(('Inbox', *labels))}\r\n"
        f"Message-ID: <estimate-{number}@example.net>\r\n"
        f"Date: {date}\r\n"
        f"From: {SALT} <q@example.net>\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        f"Subject: {SALT} {number}\r\n"
        "\r\n"
        f"{SALT} says hello.\r\n"
    ).encode()


NOTE = (
    "Vault-Path: Daily/2026-10-05.md\nCaptured-At: 2026-10-06T01:00:00-05:00\n\n"
    f"## Plans\n{SALT} came by.\n"
).encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data root whose L0 holds three messages and a note since the window start (one
    message a promotion, never offered) and one message from before it."""
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    gmail, obsidian = root / "inbox" / "gmail", root / "inbox" / "obsidian"
    gmail.mkdir(parents=True)
    obsidian.mkdir(parents=True)
    (gmail / "1.eml").write_bytes(mail(1, "Mon, 5 Oct 2026 09:00:00 -0500"))
    (gmail / "2.eml").write_bytes(mail(2, "Tue, 6 Oct 2026 09:00:00 -0500", "Category Promotions"))
    (gmail / "3.eml").write_bytes(mail(3, "Wed, 7 Oct 2026 09:00:00 -0500", "Category Updates"))
    (gmail / "4.eml").write_bytes(mail(4, "Mon, 28 Sep 2026 09:00:00 -0500"))
    (obsidian / "2026-10-05.md").write_bytes(NOTE)
    assert main(["ingest"]) == 0
    return root


def a_replay(data_root: Path, name: str, *costs: tuple[float | None, int]) -> None:
    """A replay's run log under the data root's replays, a committed run per (cost, offered)."""
    log = data_root / "replays" / name / "run-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as out:
        for cost, offered in costs:
            entry = {
                "status": "committed",
                "commit": "0" * 40,
                "occurred_from": "2026-08-24T05:00:00+00:00",
                "occurred_to": "2026-08-31T05:00:00+00:00",
                "episodes": offered,
                "skipped": 0,
                "librarian_version": "665463df6ee7",
                "model": "claude-opus-5-5",
                "effort": "high",
                "sessions": [],
                "cost_usd": cost,
                "error": None,
            }
            out.write(json.dumps(entry) + "\n")


def estimate(capsys: pytest.CaptureFixture[str]) -> list[str]:
    capsys.readouterr()
    assert main(["backfill-estimate", "--window-start", WINDOW_START]) == 0
    output = capsys.readouterr()
    assert SALT not in output.out + output.err
    return output.out.splitlines()


def test_the_estimate_counts_each_source_and_gmail_category_since_the_window_start(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    lines = estimate(capsys)

    assert lines[:2] == [
        "backfill: gmail: 3 episodes since 2026-10-01: "
        "primary 1, promotions 1, social 0, updates 1, forums 0",
        "backfill: obsidian: 1 episode since 2026-10-01",
    ]


def test_without_a_replay_the_cost_is_estimated_at_the_scale_corpus_rate(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    lines = estimate(capsys)

    # Offered: the Primary and Updates mail, and the note; never the promotion.
    assert lines[-1] == (
        "backfill: a replay from 2026-10-01 would offer 3 episodes: about $0.19, at $0.064 per "
        "offered episode (the scale corpus's reference replay)"
    )


def test_the_cost_is_estimated_at_the_last_replays_rate_per_offered_episode(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    a_replay(data_root, "older", (99.0, 10))
    older = data_root / "replays" / "older" / "run-log.jsonl"
    os.utime(older, (older.stat().st_atime, older.stat().st_mtime - 3600))
    a_replay(data_root, "backfill", (6.0, 20), (None, 5), (4.0, 20))

    lines = estimate(capsys)

    # $10 over the 40 episodes of the runs that reported a cost.
    assert lines[-1] == (
        "backfill: a replay from 2026-10-01 would offer 3 episodes: about $0.75, at $0.250 per "
        "offered episode (the last replay's, backfill)"
    )


def test_a_backfill_ends_with_the_estimate(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    for var in ("PA_VAULT_DIR", "PA_GMAIL_TOKEN", "PA_ICLOUD_CALENDARS"):
        monkeypatch.delenv(var, raising=False)
    capsys.readouterr()

    assert main(["capture", "--backfill", WINDOW_START]) == 0

    out = capsys.readouterr().out
    assert "backfill: gmail: 3 episodes since 2026-10-01" in out
    assert out.splitlines()[-1].startswith("backfill: a replay from 2026-10-01 would offer 3")
