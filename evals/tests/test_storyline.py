from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from pa_core.owner import Owner
from pa_evals.storyline import SpecError, StorylineSpec, load_spec

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


def sent_mail_with_a_category(spec: Spec) -> None:
    event(spec, "swim-registration-question")["category"] = "updates"


def unknown_gmail_category(spec: Spec) -> None:
    event(spec, "wedding-hotel-block")["category"] = "primary"


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
        (sent_mail_with_a_category, "swim-registration-question: the owner's sent mail has no"),
        (unknown_gmail_category, "'promotions', 'social', 'updates' or 'forums'"),
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


# An extension: storyline spec files loaded together with a base spec.

SAMPLE_EXTENSION = Path(__file__).parent / "fixtures" / "sample_extension"
EXTENSION_FILES = ("garden.yaml", "nursery.yaml")


def extension() -> dict[str, Spec]:
    return {name: yaml.safe_load((SAMPLE_EXTENSION / name).read_text()) for name in EXTENSION_FILES}


def load_extended(tmp_path: Path, base: Spec, files: dict[str, Spec]) -> StorylineSpec:
    paths: list[Path] = []
    for name, data in {"base.yaml": base, **files}.items():
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data))
        paths.append(path)
    return load_spec(*paths)


def test_an_extension_spread_over_several_files_loads_with_its_base() -> None:
    spec = load_spec(SAMPLE_SPEC, *(SAMPLE_EXTENSION / name for name in EXTENSION_FILES))

    ids = {event.id for event in spec.events}
    assert {"swim-registration-opens", "garden-quote", "nursery-order"} <= ids
    assert len(ids) == 11 + 6 + 4
    # Each file's events may name people and calendars from any of the files.
    assert spec.person("yuki").name == "Yuki Tanaka"
    assert spec.calendar("garden").name == "Garden"
    assert spec.owner_identity == load_spec(SAMPLE_SPEC).owner_identity


def test_the_spec_lists_its_adversarial_emails() -> None:
    spec = load_spec(SAMPLE_SPEC, *(SAMPLE_EXTENSION / name for name in EXTENSION_FILES))

    assert spec.adversarial_ids == ["nursery-injection"]
    assert load_spec(SAMPLE_SPEC).adversarial_ids == []


def test_an_extension_can_give_someone_in_the_base_cast_more_names_and_addresses() -> None:
    spec = load_spec(SAMPLE_SPEC, *(SAMPLE_EXTENSION / name for name in EXTENSION_FILES))

    dave = spec.person("dave@runclub.example")
    assert dave.id == "dave"
    assert dave.aliases == ["Dave", "Davey"]
    assert dave.emails == ["dkim@example.net", "david.kim@kimlaw.example", "dave@runclub.example"]
    # A cast id still means the person's first address, as in the base spec.
    assert spec.address("dave").email == "dkim@example.net"


def first_event(data: Spec) -> dict[str, Any]:
    return data["storylines"][0]["events"][0]


def cast_id_in_two_files(base: Spec, ext: dict[str, Spec]) -> None:
    ext["nursery.yaml"]["cast"][0]["id"] = "yuki"


def cast_id_of_the_base(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["cast"][0]["id"] = "dave"


def email_address_of_the_base(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["cast"][0]["emails"] = ["dkim@example.net"]


def event_id_in_two_files(base: Spec, ext: dict[str, Spec]) -> None:
    first_event(ext["nursery.yaml"])["id"] = "garden-quote"


def event_id_of_the_base(base: Spec, ext: dict[str, Spec]) -> None:
    first_event(ext["nursery.yaml"])["id"] = "wedding-day"


def storyline_id_of_the_base(base: Spec, ext: dict[str, Spec]) -> None:
    ext["nursery.yaml"]["storylines"][0]["id"] = "wedding"


def calendar_id_of_the_base(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["calendars"][0]["id"] = "family"


def extension_sets_the_owner(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["owner"] = "yuki"


def addition_for_someone_not_in_the_cast(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["cast_additions"][0]["id"] = "priya"


def address_added_twice(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["cast_additions"][0]["emails"] = ["yuki@greenthumb.example"]


@pytest.mark.parametrize(
    ("break_spec", "message"),
    [
        (cast_id_in_two_files, "cast id 'yuki' appears more than once"),
        (cast_id_of_the_base, "cast id 'dave' appears more than once"),
        (email_address_of_the_base, "email address 'dkim@example.net' appears more than once"),
        (event_id_in_two_files, "event id 'garden-quote' appears more than once"),
        (event_id_of_the_base, "event id 'wedding-day' appears more than once"),
        (storyline_id_of_the_base, "storyline id 'wedding' appears more than once"),
        (calendar_id_of_the_base, "calendar id 'family' appears more than once"),
        (extension_sets_the_owner, "owner"),
        (addition_for_someone_not_in_the_cast, "cast_additions: 'priya' is not in the cast"),
        (address_added_twice, "email address 'yuki@greenthumb.example' appears more than once"),
    ],
)
def test_an_id_used_in_more_than_one_file_is_refused(
    tmp_path: Path, break_spec: Callable[[Spec, dict[str, Spec]], None], message: str
) -> None:
    base, ext = sample(), extension()
    break_spec(base, ext)

    with pytest.raises(SpecError, match="invalid") as raised:
        load_extended(tmp_path, base, ext)
    assert message in str(raised.value)


def reply_in_a_base_thread(base: Spec, ext: dict[str, Spec]) -> None:
    del first_event(ext["garden.yaml"])["subject"]
    first_event(ext["garden.yaml"]).update(
        at="2026-10-02T08:00:00", reply_to="swim-registration-opens"
    )


def update_of_a_base_calendar_event(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["storylines"][0]["events"].append(
        {
            "id": "shift-moved-again",
            "channel": "calendar_update",
            "at": "2026-10-04T09:00:00",
            "updates": "shift-oct-5-cancelled",
            "status": "confirmed",
            "what": "The shift is back on.",
        }
    )


def correction_of_a_base_note(base: Spec, ext: dict[str, Spec]) -> None:
    ext["garden.yaml"]["storylines"][0]["events"].append(
        {
            "id": "swim-note-fixed-again",
            "channel": "note_correction",
            "at": "2026-10-05T09:00:00",
            "corrects": "swim-note-weekdays",
            "what": "Weekday sessions are on Tuesdays.",
        }
    )


def note_section_on_a_base_note_day(base: Spec, ext: dict[str, Spec]) -> None:
    event(ext["nursery.yaml"], "nursery-note")["date"] = "2026-10-03"


@pytest.mark.parametrize(
    ("break_spec", "message"),
    [
        (
            reply_in_a_base_thread,
            "event garden-quote: it replies to swim-registration-opens, from base.yaml; "
            "an extension adds no events to that file's threads",
        ),
        (
            update_of_a_base_calendar_event,
            "event shift-moved-again: it updates shift-oct-5-cancelled, from base.yaml; "
            "an extension adds no events to that file's threads",
        ),
        (
            correction_of_a_base_note,
            "event swim-note-fixed-again: it corrects swim-note-weekdays, from base.yaml; "
            "an extension adds no events to that file's threads",
        ),
        (
            note_section_on_a_base_note_day,
            "event nursery-note: base.yaml already has a daily note for 2026-10-03; an extension "
            "adds no sections to it, since that would change the note's episode id",
        ),
    ],
)
def test_an_extension_cannot_change_the_base_specs_episodes(
    tmp_path: Path, break_spec: Callable[[Spec, dict[str, Spec]], None], message: str
) -> None:
    base, ext = sample(), extension()
    break_spec(base, ext)

    with pytest.raises(SpecError, match="invalid") as raised:
        load_extended(tmp_path, base, ext)
    assert message in str(raised.value)
