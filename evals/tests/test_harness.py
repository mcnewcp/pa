import contextlib
import threading
from datetime import datetime
from pathlib import Path

import pytest

from pa_evals.agent import AgentRequest, AgentTimeoutError, ScriptedAgent
from pa_evals.eval_set import EvalSet, load_eval_set
from pa_evals.harness import run_eval
from pa_evals.judge import ScriptedJudge, Verdict
from pa_evals.results import RunResults

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"

DEADLINE_EMAIL = "gmail_94f6b4cbc55a3b85"
FIRST_EMAIL = "gmail_424bdea9b0d82720"
SWIM_EVENT = "icloud_calendar_9d69b653f9a42b18"
SWIM_NOTE = "obsidian_0510599eb323eee0"


@pytest.fixture
def eval_set() -> EvalSet:
    return load_eval_set(FIXTURES / "eval_set.json")


def correct(reasoning: str = "Matches the expected answer.", *relevant: str) -> Verdict:
    return Verdict(correct=True, reasoning=reasoning, relevant_citations=list(relevant))


def run(eval_set: EvalSet, answers: dict, verdicts: dict, **kwargs) -> RunResults:
    return run_eval(CORPUS, eval_set, ScriptedAgent(answers), ScriptedJudge(verdicts), **kwargs)


def test_an_answer_citing_all_required_evidence_scores_full_recall_and_validity(eval_set):
    eval_set = eval_set.only("hotel-block-deadline")
    answers = {"hotel-block-deadline": f"It closes on Oct 9 [ep:{DEADLINE_EMAIL}]."}
    verdicts = {"hotel-block-deadline": correct("Right date.", DEADLINE_EMAIL)}

    [result] = run(eval_set, answers, verdicts).questions

    assert result.status == "scored"
    assert result.cited == [DEADLINE_EMAIL]
    assert result.evidence_recall == 1.0
    assert result.citation_validity == 1.0
    assert result.correct is True
    assert result.judge_reasoning == "Right date."


def test_an_answer_citing_part_of_the_evidence_scores_partial_recall(eval_set):
    eval_set = eval_set.only("swim-lessons-plan")
    answers = {"swim-lessons-plan": f"Saturdays at 9 [ep:{SWIM_EVENT}]."}
    verdicts = {"swim-lessons-plan": correct("Has the time.", SWIM_EVENT)}

    [result] = run(eval_set, answers, verdicts).questions

    assert result.evidence_recall == 0.5
    assert result.citation_validity == 1.0


def test_a_made_up_citation_counts_against_validity_and_is_not_shown_to_the_judge(eval_set):
    eval_set = eval_set.only("hotel-block-deadline")
    answers = {"hotel-block-deadline": f"Oct 9 [ep:{DEADLINE_EMAIL}] [ep:gmail_0000000000000000]."}
    # Even a judge that calls the made-up id relevant cannot make it valid.
    verdicts = {"hotel-block-deadline": correct("ok", DEADLINE_EMAIL, "gmail_0000000000000000")}
    judge = ScriptedJudge(verdicts)

    [result] = run_eval(CORPUS, eval_set, ScriptedAgent(answers), judge).questions

    assert result.evidence_recall == 1.0
    assert result.citation_validity == 0.5
    assert result.unknown_citations == ["gmail_0000000000000000"]
    [request] = judge.requests
    assert [episode.episode_id for episode in request.cited_episodes] == [DEADLINE_EMAIL]
    assert "Oct 9" in request.cited_episodes[0].body


def test_a_citation_the_judge_finds_irrelevant_counts_against_validity(eval_set):
    eval_set = eval_set.only("hotel-block-deadline")
    answers = {"hotel-block-deadline": f"Oct 9 [ep:{DEADLINE_EMAIL}] [ep:{SWIM_NOTE}]."}
    verdicts = {"hotel-block-deadline": correct("ok", DEADLINE_EMAIL)}

    [result] = run(eval_set, answers, verdicts).questions

    assert result.citation_validity == 0.5
    assert result.irrelevant_citations == [SWIM_NOTE]
    assert result.unknown_citations == []


def test_an_answer_with_no_citations_has_no_validity_and_zero_recall(eval_set):
    eval_set = eval_set.only("hotel-block-deadline", "swim-instructor")
    answers = {"hotel-block-deadline": "Oct 9.", "swim-instructor": "I don't know."}
    verdicts = {"hotel-block-deadline": correct(), "swim-instructor": correct()}

    deadline, instructor = run(eval_set, answers, verdicts).questions

    assert (deadline.evidence_recall, deadline.citation_validity) == (0.0, None)
    # An abstention question requires no evidence, so recall does not apply.
    assert (instructor.evidence_recall, instructor.citation_validity) == (None, None)


def test_a_failing_agent_is_recorded_as_failed_and_other_questions_still_score(eval_set):
    eval_set = eval_set.only("hotel-block-deadline", "who-asked-about-hotel")
    answers = {
        "hotel-block-deadline": AgentTimeoutError("no answer within 300 s"),
        "who-asked-about-hotel": f"You did, on Sep 14 [ep:{FIRST_EMAIL}].",
    }
    verdicts = {"who-asked-about-hotel": correct("Right person.", FIRST_EMAIL)}

    results = run(eval_set, answers, verdicts)
    failed, scored = results.questions

    assert failed.status == "failed"
    assert failed.error == "agent: no answer within 300 s"
    assert (failed.answer, failed.correct, failed.evidence_recall) == (None, None, 0.0)
    assert scored.status == "scored"
    assert scored.correct is True
    assert results.overall.failed == 1
    assert results.overall.correctness == 0.5


def test_an_unexpected_agent_crash_is_recorded_as_failed(eval_set):
    eval_set = eval_set.only("hotel-block-deadline")
    answers = {"hotel-block-deadline": RuntimeError("boom")}

    [result] = run(eval_set, answers, {}).questions

    assert result.status == "failed"
    assert result.error == "agent: RuntimeError: boom"


def test_a_failing_judge_is_recorded_as_failed_but_keeps_the_answer(eval_set):
    eval_set = eval_set.only("hotel-block-deadline")
    answers = {"hotel-block-deadline": f"Oct 9 [ep:{DEADLINE_EMAIL}]."}

    [result] = run(eval_set, answers, {}).questions

    assert result.status == "failed"
    assert result.error == "judge: no scripted verdict for question hotel-block-deadline"
    assert result.answer == f"Oct 9 [ep:{DEADLINE_EMAIL}]."
    assert result.evidence_recall == 1.0
    assert result.correct is None


def test_scores_roll_up_per_category_in_eval_set_order(eval_set):
    answers = {
        "hotel-block-deadline": f"Oct 9 [ep:{DEADLINE_EMAIL}].",
        "swim-lessons-plan": f"Saturdays [ep:{SWIM_EVENT}] [ep:{SWIM_NOTE}].",
        "who-asked-about-hotel": "Mara did.",
        "swim-instructor": "I don't know.",
        "day-of-week": "Saturday.",
    }
    wrong = Verdict(correct=False, reasoning="Wrong person.", relevant_citations=[])
    verdicts = {
        "hotel-block-deadline": correct("ok", DEADLINE_EMAIL),
        "swim-lessons-plan": correct("ok", SWIM_EVENT),
        "who-asked-about-hotel": wrong,
        "swim-instructor": correct(),
        "day-of-week": correct(),
    }

    results = run(eval_set, answers, verdicts)

    assert list(results.categories) == [
        "single_fact_recall",
        "cross_channel_synthesis",
        "attribution",
        "abstention",
        "canary",
    ]
    assert results.categories["attribution"].correctness == 0.0
    assert results.categories["cross_channel_synthesis"].citation_validity == 0.5
    assert results.overall.questions == 5
    assert results.overall.correctness == 0.8
    # Recall over the three questions that require evidence: 1, 1, 0.
    assert results.overall.evidence_recall == pytest.approx(2 / 3)
    # Validity over the two answers that cite anything: 1 and 0.5.
    assert results.overall.citation_validity == 0.75


def test_the_agent_is_asked_from_the_as_of_time_against_the_ingested_corpus(eval_set):
    eval_set = eval_set.only("hotel-block-deadline", "day-of-week")
    seen: dict[str, tuple] = {}

    class LookingAgent:
        def answer(self, request: AgentRequest) -> str:
            episodes = sorted(p.stem for p in request.data_root.l0.glob("episodes/*/*/*.json"))
            seen[request.question_id] = (request.as_of, request.owner.name, episodes)
            return "Saturday."

    verdicts = {"hotel-block-deadline": correct(), "day-of-week": correct()}

    results = run_eval(CORPUS, eval_set, LookingAgent(), ScriptedJudge(verdicts))

    all_episodes = sorted([DEADLINE_EMAIL, FIRST_EMAIL, SWIM_EVENT, SWIM_NOTE])
    assert seen["hotel-block-deadline"] == (eval_set.as_of, "Argus McNevans", all_episodes)
    assert seen["day-of-week"][0] == datetime.fromisoformat("2026-10-10T09:00:00-05:00")
    assert results.ingest.model_dump() == {"ingested": 4, "unchanged": 0, "quarantined": 0}


def test_questions_run_concurrently_up_to_the_limit(eval_set):
    # An even number of questions, so every pair of workers meets at the barrier.
    eval_set = eval_set.only(*[q.id for q in eval_set.questions[:4]])
    lock = threading.Lock()
    running = peak = 0
    both_running = threading.Barrier(2, timeout=5)

    class SlowAgent:
        def answer(self, request: AgentRequest) -> str:
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            with contextlib.suppress(threading.BrokenBarrierError):
                both_running.wait()
            with lock:
                running -= 1
            return "answer"

    verdicts = {q.id: correct() for q in eval_set.questions}

    run_eval(CORPUS, eval_set, SlowAgent(), ScriptedJudge(verdicts), concurrency=2)

    assert peak == 2


def test_each_run_uses_a_fresh_throwaway_data_root_outside_pa_data_dir(
    eval_set, tmp_path, monkeypatch
):
    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.setenv("PA_DATA_DIR", str(configured))
    roots: list[Path] = []

    class RecordingAgent:
        def answer(self, request: AgentRequest) -> str:
            roots.append(request.data_root.path)
            return "answer"

    eval_set = eval_set.only("hotel-block-deadline")
    run_eval(CORPUS, eval_set, RecordingAgent(), ScriptedJudge({}))
    run_eval(CORPUS, eval_set, RecordingAgent(), ScriptedJudge({}))

    first, second = roots
    assert first != second
    assert not first.exists()
    assert not second.exists()
    assert not first.is_relative_to(configured)
    assert list(configured.iterdir()) == []
