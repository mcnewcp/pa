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
from pa_evals.questions import build_eval_set, load_questions
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
def harness_l0(eval_set: EvalSet) -> tuple[IngestCounts, dict[str, str]]:
    """The whole frozen corpus ingested by the eval harness, and each episode in its L0 by id.

    An episode is the text of its files (the envelope and the raw payload), as the agent's file
    search reads them.
    """
    episodes: dict[str, str] = {}

    class ListingAgent:
        def answer(self, request: AgentRequest) -> str:
            l0 = request.data_root.l0
            for envelope in FilesystemL0(l0).envelopes():
                files = sorted(l0.rglob(f"{envelope.episode_id}.*"))
                episodes[envelope.episode_id] = "\n".join(p.read_text("utf-8") for p in files)
            return "answer"

    one_question = eval_set.only(eval_set.questions[0].id)
    results = run_eval(frozen.PAYLOADS, one_question, ListingAgent(), ScriptedJudge({}))
    return results.ingest, episodes


@pytest.fixture(scope="module")
def ingested(harness_l0: tuple[IngestCounts, dict[str, str]]) -> tuple[IngestCounts, set[str]]:
    """The ingest counts, and the episode ids in L0."""
    counts, episodes = harness_l0
    return counts, set(episodes)


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
    assert len(measured) == 9
    assert {category: counts[category] for category in measured if counts[category] < 2} == {}
    assert 1 <= counts["canary"] <= 2
    assert 22 <= len(eval_set.questions) <= 28


def test_the_paraphrase_category_has_one_question_per_storyline(spec):
    storyline_of = {
        event.id: storyline.id for storyline in spec.storylines for event in storyline.events
    }
    questions = load_questions(frozen.QUESTIONS).questions
    paraphrases = [q for q in questions if q.category == "paraphrase"]

    storylines = [{storyline_of[event] for event in q.evidence} for q in paraphrases]
    assert sorted(storylines, key=sorted) == [{"family"}, {"kitchen"}, {"medical"}, {"wedding"}]


PARAPHRASE_KEY_WORDS = {
    "texas-flight-times": ["plane", "land", "Texas", "home"],
    "cabinet-guy-update": ["cabinet", "guy", "updated", "price"],
    "leg-specialist": ["leg", "specialist", "doc", "looked"],
    "hitched-lodging": ["crashing", "north", "buddy", "hitched"],
}
"""The words a lexical search would try for each paraphrase question."""


def test_a_plain_grep_for_a_paraphrase_questions_key_words_misses_its_evidence(
    harness_l0, eval_set: EvalSet
):
    _, episodes = harness_l0
    paraphrases = [q for q in eval_set.questions if q.category == "paraphrase"]
    assert {q.id for q in paraphrases} == set(PARAPHRASE_KEY_WORDS)

    for question in paraphrases:
        for episode_id in question.evidence:
            # Case-insensitive, like `grep -ril` over L0.
            text = episodes[episode_id].lower()
            hits = [w for w in PARAPHRASE_KEY_WORDS[question.id] if w.lower() in text]
            assert hits == [], f"{question.id}: {episode_id} contains {hits}"


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
