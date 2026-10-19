"""Checks on the scale corpus's ground truth: its storyline extension and new questions.

The extension is only spec until it is generated and frozen; these checks hold before then.
"""

import datetime as dt
from collections import Counter

import pytest

from pa_evals import frozen
from pa_evals.questions import Questions, load_questions
from pa_evals.storyline import (
    BulkEmail,
    CalendarEvent,
    CalendarUpdate,
    ChatEvent,
    EmailEvent,
    NoteCorrection,
    NoteSection,
    StorylineSpec,
    load_spec,
)


@pytest.fixture(scope="module")
def argus() -> StorylineSpec:
    return load_spec(frozen.SPEC)


@pytest.fixture(scope="module")
def scale() -> StorylineSpec:
    # Loading runs the validator, including the rules that protect Argus's payloads.
    return load_spec(frozen.SPEC, *frozen.SCALE_EXTENSION)


@pytest.fixture(scope="module")
def questions() -> Questions:
    return load_questions(frozen.SCALE_QUESTIONS)


def test_the_extension_leaves_every_argus_storyline_as_it_is(argus, scale):
    assert frozen.SCALE_EXTENSION, "no scale-*.yaml files beside argus.yaml"
    added = {storyline.id for storyline in scale.storylines} - {s.id for s in argus.storylines}
    assert len(added) == len(scale.storylines) - len(argus.storylines)
    for storyline in argus.storylines:
        assert next(s for s in scale.storylines if s.id == storyline.id) == storyline


def test_the_two_long_storylines_each_have_twenty_or_more_touches(scale):
    touches = {storyline.id: len(storyline.events) for storyline in scale.storylines}

    assert touches["basement"] >= 20
    assert touches["cabin"] >= 20


def test_the_extension_has_every_source_shape_at_about_the_agreed_scale(scale):
    added = Counter(type(event).__name__ for event in scale.events if not scale.in_base(event.id))
    note_days = {
        event.date
        for event in scale.events
        if isinstance(event, NoteSection) and not scale.in_base(event.id)
    }

    assert 800 <= added[BulkEmail.__name__] <= 900
    assert 130 <= added[EmailEvent.__name__] <= 170
    assert 85 <= added[CalendarEvent.__name__] + added[CalendarUpdate.__name__] <= 115
    assert 75 <= len(note_days) <= 90
    assert 18 <= added[ChatEvent.__name__] <= 22
    assert len(scale.adversarial_ids) == 5


def test_every_bulk_category_appears_and_adversarial_mail_lands_in_primary(scale):
    categories = {email.category for email in scale.bulk_emails}
    adversarial = [scale.event(event_id) for event_id in scale.adversarial_ids]

    assert categories == {"promotions", "social", "updates", "forums", None}
    assert all(isinstance(e, EmailEvent) and e.category is None for e in adversarial)


def test_everything_in_the_extension_is_captured_before_the_as_of_time(scale, questions):
    def captured(event) -> dt.datetime:
        match event:
            case NoteSection():
                return scale.note_captured_at(event.date)
            case BulkEmail():
                return event.at.replace(tzinfo=scale.zone)
            case EmailEvent() | CalendarEvent() | CalendarUpdate() | NoteCorrection() | ChatEvent():
                return scale.local(event.at)
        raise AssertionError(event)

    late = [event.id for event in scale.events if captured(event) >= questions.as_of]
    assert late == []


def test_no_calendar_event_in_the_extension_lies_after_the_as_of_time(scale, questions):
    # Future events would change the Argus questions about upcoming Saturdays.
    def last_day(event: CalendarEvent | CalendarUpdate) -> dt.date | None:
        end = event.end or event.start
        return end.date() if isinstance(end, dt.datetime) else end

    late = [
        event.id
        for event in scale.events
        if isinstance(event, CalendarEvent | CalendarUpdate)
        and not scale.in_base(event.id)
        and (day := last_day(event)) is not None
        and day > questions.as_of.date()
    ]
    assert late == []


def test_the_new_questions_match_the_agreed_mix(questions):
    counts = Counter(question.category for question in questions.questions)

    assert len(questions.questions) == 15
    assert counts == {
        "timeline_narrative": 3,
        "attribution": 2,
        "open_loops": 3,
        "entity_resolution": 2,
        "connection": 3,
        "single_fact_recall": 1,  # told only to the assistant
        "abstention": 1,
    }


def test_the_new_questions_are_asked_at_the_argus_as_of_time(questions):
    assert questions.as_of == load_questions(frozen.QUESTIONS).as_of
    assert all(question.as_of is None for question in questions.questions)


def test_every_new_question_rests_on_events_in_the_scale_spec(scale, questions):
    missing = [
        f"{question.id}: {event_id}"
        for question in questions.questions
        for event_id in question.evidence
        if scale.find_event(event_id) is None
    ]
    assert missing == []
    for question in questions.questions:
        assert bool(question.evidence) == (question.category != "abstention"), question.id


def test_the_question_about_something_told_only_to_the_assistant_rests_on_a_chat(scale, questions):
    [question] = [q for q in questions.questions if q.id == "basement-paint"]

    assert [type(scale.event(e)) for e in question.evidence] == [ChatEvent]


def test_the_new_question_ids_differ_from_the_argus_ones(questions):
    argus_ids = {question.id for question in load_questions(frozen.QUESTIONS).questions}

    assert argus_ids.isdisjoint(question.id for question in questions.questions)
