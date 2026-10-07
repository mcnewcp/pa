from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.model_client import FakeModelBackend, InvalidModelOutputError, ModelClient
from pa_core.normalizers import normalizer_for
from pa_core.owner import Owner
from pa_evals.judge import JudgeRequest, ModelJudge, Verdict

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))


def episode(name: str):
    raw = (CORPUS / name).read_bytes()
    return normalizer_for(name, OWNER)(raw, captured_at=datetime(2026, 10, 6, tzinfo=UTC))


def request(answer: str = "Oct 9 [ep:gmail_94f6b4cbc55a3b85].") -> JudgeRequest:
    return JudgeRequest(
        question_id="hotel-block-deadline",
        question="When does the hotel block for Dave's wedding close?",
        expected_answer="October 9.",
        as_of=datetime.fromisoformat("2026-10-06T20:00:00-05:00"),
        answer=answer,
        cited_episodes=(episode("reply_with_cc.eml"),),
    )


def test_the_model_judge_returns_the_models_verdict():
    reply = {"correct": True, "reasoning": "Same date.", "relevant_citations": ["gmail_94f6"]}
    judge = ModelJudge(ModelClient(FakeModelBackend(reply)))

    verdict = judge.judge(request())

    assert verdict == Verdict(
        correct=True, reasoning="Same date.", relevant_citations=["gmail_94f6"]
    )


def test_the_model_judge_shows_the_model_the_question_answer_and_cited_episodes():
    backend = FakeModelBackend({"correct": True, "reasoning": "ok", "relevant_citations": []})

    ModelJudge(ModelClient(backend)).judge(request(answer="It closes Oct 9."))

    [sent] = backend.requests
    for expected in (
        "When does the hotel block for Dave's wedding close?",
        "October 9.",
        "It closes Oct 9.",
        "2026-10-06",
        "gmail_94f6b4cbc55a3b85",
        "Re: Hotel block for the wedding",
        "The hotel block closes on Oct 9",
        "dkim@example.net",
    ):
        assert expected in sent.prompt
    assert sent.schema == Verdict.model_json_schema()


def test_the_model_judge_raises_when_the_model_keeps_replying_badly():
    judge = ModelJudge(ModelClient(FakeModelBackend("not json", "still not json")))

    with pytest.raises(InvalidModelOutputError):
        judge.judge(request())
