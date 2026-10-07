from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from pa_core.owner import Owner
from pa_evals.storyline import SpecError, load_spec

SAMPLE_SPEC = Path(__file__).parent / "fixtures" / "sample_spec.yaml"

type Spec = dict[str, Any]


def sample() -> Spec:
    return yaml.safe_load(SAMPLE_SPEC.read_text())


def event(spec: Spec, event_id: str) -> dict[str, Any]:
    return next(e for s in spec["storylines"] for e in s["events"] if e["id"] == event_id)


def write(tmp_path: Path, spec: Spec) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def test_the_spec_owner_is_the_owner_ingest_needs() -> None:
    assert load_spec(SAMPLE_SPEC).owner_identity == Owner(
        name="Argus McNevans",
        email_addresses=("argus@example.com", "argus.mcnevans@example.org"),
        other_names=("Gus",),
    )


def unknown_recipient(spec: Spec) -> None:
    event(spec, "wedding-hotel-block")["to"].append("priya")


def reply_before_its_parent(spec: Spec) -> None:
    event(spec, "swim-registration-question")["at"] = "2026-10-01T08:00:00"


def new_email_without_a_subject(spec: Spec) -> None:
    del event(spec, "wedding-hotel-block")["subject"]


def reply_to_a_calendar_event(spec: Spec) -> None:
    event(spec, "swim-registration-question")["reply_to"] = "wedding-day"


def update_before_the_event(spec: Spec) -> None:
    event(spec, "shift-oct-5-moved")["at"] = "2026-09-01T08:00:00"


def two_updates_of_one_version(spec: Spec) -> None:
    event(spec, "shift-oct-5-cancelled")["updates"] = "shift-oct-5"


def unknown_calendar(spec: Spec) -> None:
    event(spec, "wedding-day")["calendar"] = "work"


def duplicate_event_id(spec: Spec) -> None:
    event(spec, "wedding-note")["id"] = "swim-note-saturdays"


def duplicate_heading_on_a_day(spec: Spec) -> None:
    event(spec, "wedding-note")["heading"] = "Swim lessons"


def correction_before_capture(spec: Spec) -> None:
    event(spec, "swim-note-correction")["at"] = "2026-10-04T00:30:00"


def shift_ending_before_it_starts(spec: Spec) -> None:
    event(spec, "shift-oct-5-moved")["end"] = "2026-10-06T14:00:00"


def all_day_event_with_a_timed_end(spec: Spec) -> None:
    event(spec, "wedding-day")["end"] = "2026-10-25T12:00:00"


def owner_not_in_cast(spec: Spec) -> None:
    spec["owner"] = "gus"


def unknown_time_zone(spec: Spec) -> None:
    spec["timezone"] = "America/Gotham"


def event_without_what(spec: Spec) -> None:
    del event(spec, "wedding-day")["what"]


def unknown_channel(spec: Spec) -> None:
    event(spec, "wedding-day")["channel"] = "sms"


@pytest.mark.parametrize(
    ("break_spec", "message"),
    [
        (unknown_recipient, "'priya' is not a cast id or a cast email address"),
        (reply_before_its_parent, "sent before swim-registration-opens"),
        (new_email_without_a_subject, "wedding-hotel-block: it needs a subject"),
        (reply_to_a_calendar_event, "reply_to 'wedding-day' is not an email event"),
        (update_before_the_event, "made before shift-oct-5, which it updates"),
        (two_updates_of_one_version, "shift-oct-5 is already updated by another event"),
        (unknown_calendar, "calendar 'work' is not in calendars"),
        (duplicate_event_id, "event id 'swim-note-saturdays' appears more than once"),
        (duplicate_heading_on_a_day, "the 2026-10-03 note already has 'Swim lessons'"),
        (correction_before_capture, "before the 2026-10-03 note is captured"),
        (shift_ending_before_it_starts, "shift-oct-5-moved: it ends before it starts"),
        (all_day_event_with_a_timed_end, "both be dates or both be times"),
        (owner_not_in_cast, "owner 'gus' is not in the cast"),
        (unknown_time_zone, "unknown time zone 'America/Gotham'"),
        (event_without_what, "what"),
        (unknown_channel, "sms"),
    ],
)
def test_an_inconsistent_spec_is_refused_saying_why(
    tmp_path: Path, break_spec: Callable[[Spec], None], message: str
) -> None:
    spec = sample()
    break_spec(spec)

    with pytest.raises(SpecError, match="invalid") as raised:
        load_spec(write(tmp_path, spec))
    assert message in str(raised.value)


def test_an_unreadable_spec_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text("cast: [unclosed")

    with pytest.raises(SpecError, match="cannot read storyline spec"):
        load_spec(path)
