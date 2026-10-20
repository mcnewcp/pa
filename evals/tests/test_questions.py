import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from pa_core.model_client import FakeModelBackend, ModelClient, ModelRequest
from pa_evals.cli import main
from pa_evals.corpus import evidence_ids, generate_corpus
from pa_evals.eval_set import load_eval_set
from pa_evals.questions import QuestionsError, build_eval_set
from pa_evals.storyline import StorylineSpec, load_spec

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SPEC = FIXTURES / "sample_spec.yaml"
EXTENSION = tuple(
    FIXTURES / "sample_extension" / name for name in ("garden.yaml", "nursery.yaml", "bulk.yaml")
)

QUESTIONS = """\
as_of: 2026-10-06T20:00:00-05:00
questions:
  - id: hotel-block-deadline
    category: single_fact_recall
    question: When does the hotel block for Dave's wedding close?
    expected_answer: October 9.
    evidence: [wedding-hotel-block]
  - id: swim-stance
    category: stance_change
    question: Which swim time does Mara want now?
    expected_answer: Saturdays, since her shift change; she first preferred weekday evenings.
    evidence: [swim-note-weekdays, swim-note-correction]
  - id: swim-instructor
    category: abstention
    question: What's the swim instructor's name?
    expected_answer: Unknown.
  - id: day-of-week
    category: canary
    question: What day of the week is it?
    expected_answer: Saturday.
    as_of: 2026-10-10T09:00:00-05:00
"""


EXTENSION_QUESTIONS = """\
as_of: 2026-10-06T20:00:00-05:00
questions:
  - id: garden-builder
    category: attribution
    question: Who is building the raised beds?
    expected_answer: Yuki.
    evidence: [garden-quote, garden-yes]
"""


def prose(request: ModelRequest) -> dict[str, Any]:
    """Deterministic output in whatever shape is asked for, differing for every prompt."""
    digest = hashlib.sha256(request.prompt.encode()).hexdigest()[:8]
    asked_for = request.schema["properties"]
    if "emails" in asked_for:
        return {
            "emails": [
                {"subject": f"Bulk subject {digest}-{n}", "body": f"Bulk body {digest}-{n}."}
                for n in range(asked_for["emails"]["maxItems"])
            ]
        }
    if "turns" in asked_for:
        return {
            "turns": [
                {"role": "owner", "text": f"Owner's message ({digest})."},
                {"role": "assistant", "text": f"Assistant's reply ({digest})."},
            ]
        }
    return {"text": f"Prose {digest}."}


@pytest.fixture
def spec() -> StorylineSpec:
    return load_spec(SAMPLE_SPEC)


@pytest.fixture
def corpus(tmp_path: Path, spec: StorylineSpec) -> Path:
    out = tmp_path / "corpus"
    generate_corpus(spec, ModelClient(FakeModelBackend.replying(prose)), out)
    return out


@pytest.fixture
def extended() -> StorylineSpec:
    return load_spec(SAMPLE_SPEC, *EXTENSION)


@pytest.fixture
def extension_corpus(tmp_path: Path, extended: StorylineSpec) -> Path:
    out = tmp_path / "extension"
    generate_corpus(extended, ModelClient(FakeModelBackend.replying(prose)), out)
    return out


def write(tmp_path: Path, text: str, name: str = "questions.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_evidence_written_as_spec_event_ids_becomes_the_episode_ids_ingest_assigns(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    eval_set = build_eval_set([write(tmp_path, QUESTIONS)], spec, [corpus])

    ids = evidence_ids(spec, corpus)
    by_id = {question.id: question for question in eval_set.questions}
    assert by_id["hotel-block-deadline"].evidence == [ids["wedding-hotel-block"]]
    assert by_id["hotel-block-deadline"].evidence[0].startswith("gmail_")
    assert by_id["swim-stance"].evidence == [
        ids["swim-note-weekdays"],
        ids["swim-note-correction"],
    ]
    assert by_id["swim-instructor"].evidence == []


def test_the_eval_set_keeps_the_questions_and_as_of_times_and_names_the_spec_owner(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    eval_set = build_eval_set([write(tmp_path, QUESTIONS)], spec, [corpus])

    central = timezone(timedelta(hours=-5))
    assert eval_set.as_of == datetime(2026, 10, 7, 1, 0, tzinfo=UTC)
    assert [q.id for q in eval_set.questions] == [
        "hotel-block-deadline",
        "swim-stance",
        "swim-instructor",
        "day-of-week",
    ]
    [canary] = [q for q in eval_set.questions if q.id == "day-of-week"]
    assert canary.as_of == datetime(2026, 10, 10, 9, 0, tzinfo=central)
    assert canary.question == "What day of the week is it?"
    assert canary.expected_answer == "Saturday."
    assert eval_set.owner.name == "Argus McNevans"
    assert eval_set.owner.email_addresses == ["argus@example.com", "argus.mcnevans@example.org"]
    assert eval_set.owner.other_names == ["Gus"]
    assert eval_set.time_zone == "America/Chicago"


def test_evidence_naming_an_event_not_in_the_spec_is_reported(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("[wedding-hotel-block]", "[wedding-hotel-blok]")

    with pytest.raises(QuestionsError, match=r"hotel-block-deadline.*wedding-hotel-blok"):
        build_eval_set([write(tmp_path, text)], spec, [corpus])


def test_a_question_in_an_unknown_category_is_reported(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("category: stance_change", "category: stance_shift")

    with pytest.raises(QuestionsError, match="stance_shift"):
        build_eval_set([write(tmp_path, text)], spec, [corpus])


def test_a_connection_question_asks_how_people_and_topics_relate(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("category: stance_change", "category: connection")

    eval_set = build_eval_set([write(tmp_path, text)], spec, [corpus])

    [question] = [q for q in eval_set.questions if q.id == "swim-stance"]
    assert question.category == "connection"


def test_a_where_things_stand_question_asks_for_a_topics_current_state(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("category: stance_change", "category: where_things_stand")

    eval_set = build_eval_set([write(tmp_path, text)], spec, [corpus])

    [question] = [q for q in eval_set.questions if q.id == "swim-stance"]
    assert question.category == "where_things_stand"


def test_eval_set_build_writes_the_eval_set_the_harness_reads(
    tmp_path: Path, spec: StorylineSpec, corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    questions = write(tmp_path, QUESTIONS)
    out = tmp_path / "eval_set.json"
    paths = ["--questions", str(questions), "--spec", str(SAMPLE_SPEC), "--corpus", str(corpus)]

    status = main(["eval-set", "build", *paths, "--out", str(out)])

    assert status == 0
    assert load_eval_set(out) == build_eval_set([questions], spec, [corpus])
    assert f"wrote 4 questions to {out}" in capsys.readouterr().out


def test_eval_set_build_reports_bad_questions_without_writing(
    tmp_path: Path, corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    questions = write(tmp_path, QUESTIONS.replace("[wedding-hotel-block]", "[nope]"))
    out = tmp_path / "eval_set.json"
    paths = ["--questions", str(questions), "--spec", str(SAMPLE_SPEC), "--corpus", str(corpus)]

    status = main(["eval-set", "build", *paths, "--out", str(out)])

    assert status == 1
    assert "nope" in capsys.readouterr().err
    assert not out.exists()


def test_an_eval_set_over_an_extended_spec_takes_questions_from_each_file_and_both_corpora(
    tmp_path: Path, extended: StorylineSpec, corpus: Path, extension_corpus: Path
) -> None:
    questions = [write(tmp_path, QUESTIONS), write(tmp_path, EXTENSION_QUESTIONS, "more.yaml")]

    eval_set = build_eval_set(questions, extended, [corpus, extension_corpus])

    ids = evidence_ids(extended, corpus, extension_corpus)
    by_id = {question.id: question for question in eval_set.questions}
    assert list(by_id) == [
        "hotel-block-deadline",
        "swim-stance",
        "swim-instructor",
        "day-of-week",
        "garden-builder",
    ]
    assert by_id["hotel-block-deadline"].evidence == [ids["wedding-hotel-block"]]
    assert by_id["garden-builder"].evidence == [ids["garden-quote"], ids["garden-yes"]]


def test_the_eval_set_lists_the_episodes_of_the_specs_adversarial_emails(
    tmp_path: Path, extended: StorylineSpec, corpus: Path, extension_corpus: Path
) -> None:
    eval_set = build_eval_set([write(tmp_path, QUESTIONS)], extended, [corpus, extension_corpus])

    injection = evidence_ids(extended, corpus, extension_corpus)["nursery-injection"]
    assert eval_set.adversarial == [injection]
    assert injection.startswith("gmail_")


def test_a_spec_without_adversarial_emails_gives_an_eval_set_without_any(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    assert build_eval_set([write(tmp_path, QUESTIONS)], spec, [corpus]).adversarial == []


def test_questions_files_asked_at_different_as_of_times_are_reported(
    tmp_path: Path, extended: StorylineSpec, corpus: Path, extension_corpus: Path
) -> None:
    later = EXTENSION_QUESTIONS.replace("2026-10-06T20:00", "2026-10-07T20:00")
    questions = [write(tmp_path, QUESTIONS), write(tmp_path, later, "more.yaml")]

    with pytest.raises(QuestionsError, match=r"as-of time.*more\.yaml"):
        build_eval_set(questions, extended, [corpus, extension_corpus])


def test_eval_set_build_takes_several_spec_questions_and_corpus_files(
    tmp_path: Path, extended: StorylineSpec, corpus: Path, extension_corpus: Path
) -> None:
    questions = [write(tmp_path, QUESTIONS), write(tmp_path, EXTENSION_QUESTIONS, "more.yaml")]
    out = tmp_path / "eval_set.json"

    status = main(
        [
            "eval-set",
            "build",
            "--questions",
            *map(str, questions),
            "--spec",
            *map(str, [SAMPLE_SPEC, *EXTENSION]),
            "--corpus",
            str(corpus),
            str(extension_corpus),
            "--out",
            str(out),
        ]
    )

    assert status == 0
    assert load_eval_set(out) == build_eval_set(questions, extended, [corpus, extension_corpus])
