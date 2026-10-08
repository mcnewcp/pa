"""Checks on the frozen Argus corpus and its eval set, the committed data every eval run uses."""

import datetime as dt
from collections import Counter
from pathlib import Path

import pytest

from pa_evals import frozen
from pa_evals.agent import AgentRequest
from pa_evals.corpus import payload_path
from pa_evals.eval_set import CATEGORIES, EvalSet, load_eval_set
from pa_evals.harness import run_eval
from pa_evals.judge import ScriptedJudge
from pa_evals.questions import build_eval_set
from pa_evals.results import IngestCounts
from pa_evals.storyline import (
    CalendarEvent,
    CalendarUpdate,
    EmailEvent,
    NoteCorrection,
    NoteSection,
    StorylineSpec,
    load_spec,
)
from pa_home.filesystem_l0 import FilesystemL0


@pytest.fixture(scope="module")
def spec() -> StorylineSpec:
    return load_spec(frozen.SPEC)


@pytest.fixture(scope="module")
def eval_set() -> EvalSet:
    return load_eval_set(frozen.EVAL_SET)


@pytest.fixture(scope="module")
def ingested(eval_set: EvalSet) -> tuple[IngestCounts, set[str]]:
    """The whole frozen corpus ingested by the eval harness, and the episode ids in its L0."""
    episodes: set[str] = set()

    class ListingAgent:
        def answer(self, request: AgentRequest) -> str:
            store = FilesystemL0(request.data_root.l0)
            episodes.update(envelope.episode_id for envelope in store.envelopes())
            return "answer"

    one_question = eval_set.only(eval_set.questions[0].id)
    results = run_eval(frozen.PAYLOADS, one_question, ListingAgent(), ScriptedJudge({}))
    return results.ingest, episodes


def payloads() -> list[Path]:
    return sorted(
        path.relative_to(frozen.PAYLOADS) for path in frozen.PAYLOADS.rglob("*") if path.is_file()
    )


def test_ingesting_the_whole_corpus_lands_every_payload_and_quarantines_nothing(ingested):
    summary, episodes = ingested

    assert summary.quarantined == 0
    assert summary.ingested == len(payloads()) == len(episodes)


def test_every_evidence_id_in_the_eval_set_exists_in_l0(ingested, eval_set: EvalSet):
    _, episodes = ingested

    for question in eval_set.questions:
        missing = set(question.evidence) - episodes
        assert not missing, f"{question.id} needs episodes that are not in L0: {missing}"


def test_the_eval_set_is_exactly_what_the_questions_build_into(spec, eval_set: EvalSet):
    # Evidence ids are worked out from the spec and corpus, never typed by hand.
    assert eval_set == build_eval_set(frozen.QUESTIONS, spec, frozen.PAYLOADS)


def test_the_eval_set_covers_every_category_with_at_least_two_questions(eval_set: EvalSet):
    counts = Counter(question.category for question in eval_set.questions)

    measured = [category for category in CATEGORIES if category != "canary"]
    assert len(measured) == 8
    assert {category: counts[category] for category in measured if counts[category] < 2} == {}
    assert 1 <= counts["canary"] <= 2
    assert 18 <= len(eval_set.questions) <= 24


def test_abstention_and_canary_questions_need_no_evidence_and_the_rest_do(eval_set: EvalSet):
    for question in eval_set.questions:
        expects_evidence = question.category not in {"abstention", "canary"}
        assert bool(question.evidence) == expects_evidence, question.id


def test_the_eval_set_owner_is_argus_as_the_spec_names_him(spec, eval_set: EvalSet):
    assert eval_set.owner.name == "Argus McNevans"
    assert eval_set.owner.to_owner() == spec.owner_identity


def test_the_corpus_holds_exactly_the_payloads_the_spec_describes(spec):
    assert {path.as_posix() for path in payloads()} == {
        payload_path(spec, event) for event in spec.events
    }


def test_everything_in_the_corpus_was_captured_before_the_as_of_time(spec, eval_set: EvalSet):
    def captured(event) -> dt.datetime:
        match event:
            case NoteSection():
                return spec.note_captured_at(event.date)
            case EmailEvent() | CalendarEvent() | CalendarUpdate() | NoteCorrection():
                return spec.local(event.at)
        raise AssertionError(event)

    late = [event.id for event in spec.events if captured(event) >= eval_set.as_of]
    assert late == []


def test_the_spec_has_the_four_storylines_and_the_cast(spec):
    assert [storyline.id for storyline in spec.storylines] == [
        "family",
        "kitchen",
        "medical",
        "wedding",
    ]
    people = {person.id: person for person in spec.cast}
    assert people[spec.owner].name == "Argus McNevans"
    assert people["mara"].name == "Mara McNevans"
    # The friend who is "Dave" in daily notes and David Kim from two email addresses.
    assert people["dave"].name == "David Kim"
    assert people["dave"].aliases == ["Dave"]
    assert len(people["dave"].emails) == 2
    assert {calendar.name for calendar in spec.calendars} >= {"Family", "Mara Work"}
