import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pa_core.model_client import FakeModelBackend, ModelClient, ModelRequest
from pa_evals.cli import main
from pa_evals.corpus import evidence_ids, generate_corpus
from pa_evals.eval_set import load_eval_set
from pa_evals.questions import QuestionsError, build_eval_set
from pa_evals.storyline import StorylineSpec, load_spec

SAMPLE_SPEC = Path(__file__).parent / "fixtures" / "sample_spec.yaml"

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


def prose(request: ModelRequest) -> dict[str, str]:
    return {"text": f"Prose {hashlib.sha256(request.prompt.encode()).hexdigest()[:8]}."}


@pytest.fixture
def spec() -> StorylineSpec:
    return load_spec(SAMPLE_SPEC)


@pytest.fixture
def corpus(tmp_path: Path, spec: StorylineSpec) -> Path:
    out = tmp_path / "corpus"
    generate_corpus(spec, ModelClient(FakeModelBackend.replying(prose)), out)
    return out


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(text)
    return path


def test_evidence_written_as_spec_event_ids_becomes_the_episode_ids_ingest_assigns(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    eval_set = build_eval_set(write(tmp_path, QUESTIONS), spec, corpus)

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
    eval_set = build_eval_set(write(tmp_path, QUESTIONS), spec, corpus)

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


def test_evidence_naming_an_event_not_in_the_spec_is_reported(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("[wedding-hotel-block]", "[wedding-hotel-blok]")

    with pytest.raises(QuestionsError, match=r"hotel-block-deadline.*wedding-hotel-blok"):
        build_eval_set(write(tmp_path, text), spec, corpus)


def test_a_question_in_an_unknown_category_is_reported(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    text = QUESTIONS.replace("category: stance_change", "category: stance_shift")

    with pytest.raises(QuestionsError, match="stance_shift"):
        build_eval_set(write(tmp_path, text), spec, corpus)


def test_eval_set_build_writes_the_eval_set_the_harness_reads(
    tmp_path: Path, spec: StorylineSpec, corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    questions = write(tmp_path, QUESTIONS)
    out = tmp_path / "eval_set.json"
    paths = ["--questions", str(questions), "--spec", str(SAMPLE_SPEC), "--corpus", str(corpus)]

    status = main(["eval-set", "build", *paths, "--out", str(out)])

    assert status == 0
    assert load_eval_set(out) == build_eval_set(questions, spec, corpus)
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
