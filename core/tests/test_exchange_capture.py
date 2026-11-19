"""Exchange capture over time: what the helper does with prompts left pending, and with an
exchange the schema refuses. The hooks themselves are fired from a rendered agent project in
adapters/home/tests/test_capture_hook.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pa_core.errors import MalformedPayloadError
from pa_core.exchange_capture import PENDING_TTL, ExchangeCapture

SESSION = "c81e5b3a-7d24-4f09-b6a2-2e9d0c4f8a17"
NOON = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def prompt(prompt_id: str, text: str, session: str = SESSION) -> dict:
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session,
        "prompt_id": prompt_id,
        "prompt": text,
    }


def stop(prompt_id: str, reply: str, session: str = SESSION) -> dict:
    return {
        "hook_event_name": "Stop",
        "session_id": session,
        "prompt_id": prompt_id,
        "last_assistant_message": reply,
    }


@pytest.fixture
def clock() -> Clock:
    return Clock(NOON)


@pytest.fixture
def capture(tmp_path: Path, clock: Clock) -> ExchangeCapture:
    (tmp_path / "inbox").mkdir()
    return ExchangeCapture(inbox=tmp_path / "inbox", state=tmp_path / "state", clock=clock)


def files_under(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file())


def test_a_prompt_left_pending_past_its_expiry_is_dropped_by_any_later_event(
    capture: ExchangeCapture, clock: Clock, tmp_path: Path
):
    # An interrupted turn's prompt in a session that never comes back: the owner's words
    # don't stay on the VM's disk.
    capture.handle("prompt", prompt("p1", "Is swim on?"))
    clock.now += PENDING_TTL + timedelta(minutes=1)

    capture.handle("prompt", prompt("p2", "When is picture day?", session="another-session"))

    [held] = files_under(tmp_path / "state")
    assert "When is picture day?" in held.read_text()


def test_a_stop_after_its_prompt_expired_writes_nothing(
    capture: ExchangeCapture, clock: Clock, tmp_path: Path
):
    capture.handle("prompt", prompt("p1", "Is swim on?"))
    clock.now += PENDING_TTL + timedelta(minutes=1)

    note = capture.handle("stop", stop("p1", "Swim starts Saturday."))

    assert note is not None and "nothing captured" in note
    assert files_under(tmp_path / "inbox") == []


def test_a_prompt_within_its_expiry_is_kept(capture: ExchangeCapture, clock: Clock, tmp_path: Path):
    capture.handle("prompt", prompt("p1", "Is swim on?"))
    clock.now += PENDING_TTL - timedelta(minutes=1)

    assert capture.handle("stop", stop("p1", "Swim starts Saturday.")) is None

    assert len(files_under(tmp_path / "inbox")) == 1


def test_an_exchange_the_schema_refuses_is_never_written(
    capture: ExchangeCapture, clock: Clock, tmp_path: Path
):
    # The clock stepped back during the turn, so the exchange would end before it started:
    # ingest would quarantine it, so it isn't written.
    capture.handle("prompt", prompt("p1", "Is swim on?"))
    clock.now -= timedelta(hours=1)

    with pytest.raises(MalformedPayloadError, match="ends before it starts"):
        capture.handle("stop", stop("p1", "Swim starts Saturday."))

    assert files_under(tmp_path / "inbox") == []


def test_the_exchange_is_timed_from_the_prompt_to_the_stop(
    capture: ExchangeCapture, clock: Clock, tmp_path: Path
):
    capture.handle("prompt", prompt("p1", "Is swim on?"))
    clock.now += timedelta(seconds=12)
    capture.handle("stop", stop("p1", "Swim starts Saturday."))

    [payload] = files_under(tmp_path / "inbox")
    text = payload.read_text()
    assert '"started_at": "2026-10-10T12:00:00.000+00:00"' in text
    assert '"ended_at": "2026-10-10T12:00:12.000+00:00"' in text
