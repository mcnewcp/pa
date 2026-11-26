import json
import os
import stat
import sys
import textwrap
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from pa_core.ingest import inbox_normalizer
from pa_core.model_client import FakeModelBackend, InvalidModelOutputError, ModelClient
from pa_core.owner import Owner
from pa_evals.judge import JudgeRequest, ModelJudge, Verdict
from pa_home.claude_model import ClaudeCliBackend

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))


def episode(path: str):
    raw = (CORPUS / path).read_bytes()
    normalize = inbox_normalizer(PurePosixPath(path), OWNER)
    return normalize(raw, captured_at=datetime(2026, 10, 6, tzinfo=UTC))


def request(answer: str = "Oct 9 [ep:gmail_94f6b4cbc55a3b85].") -> JudgeRequest:
    return JudgeRequest(
        question_id="hotel-block-deadline",
        question="When does the hotel block for Dave's wedding close?",
        expected_answer="October 9.",
        as_of=datetime.fromisoformat("2026-10-06T20:00:00-05:00"),
        answer=answer,
        cited_episodes=(episode("gmail/reply_with_cc.eml"),),
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


def test_the_rubric_asks_for_the_reasoning_before_the_ruling():
    backend = FakeModelBackend({"reasoning": "ok", "correct": True, "relevant_citations": []})

    ModelJudge(ModelClient(backend)).judge(request())

    [sent] = backend.requests
    assert sent.prompt.index("`reasoning`") < sent.prompt.index("`correct`")


def test_the_judge_is_asked_for_its_reasoning_before_its_ruling(tmp_path: Path):
    """A model fills in a structured answer in the order the schema lists its properties, so
    the schema `claude` is handed has to ask for the reasoning ahead of the ruling."""
    sent = tmp_path / "schema.json"
    claude = tmp_path / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, sys
            argv = sys.argv[1:]
            open({str(sent)!r}, "w").write(argv[argv.index("--json-schema") + 1])
            verdict = {{"correct": True, "reasoning": "Same date.", "relevant_citations": []}}
            print(json.dumps({{"type": "result", "is_error": False, "structured_output": verdict}}))
            """
        )
    )
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)

    ModelJudge(ModelClient(ClaudeCliBackend(executable=str(claude)))).judge(request())

    asked_for = json.loads(sent.read_text(), object_pairs_hook=list)
    [properties] = [value for key, value in asked_for if key == "properties"]
    assert [name for name, _ in properties] == ["reasoning", "correct", "relevant_citations"]


def test_the_model_judge_raises_when_the_model_keeps_replying_badly():
    judge = ModelJudge(ModelClient(FakeModelBackend("not json", "still not json")))

    with pytest.raises(InvalidModelOutputError):
        judge.judge(request())


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_a_citation_is_relevant_when_it_supports_its_claim_even_beyond_the_expected_answer():
    hotel, swim, question = (
        episode(path)
        for path in (
            "gmail/reply_with_cc.eml",
            "icloud_calendar/swim_lessons.ics",
            "gmail/thread_start.eml",
        )
    )
    answer = (
        f"The hotel block closes on Oct 9 [ep:{hotel.episode_id}].\n\n"
        "Also coming up: Theo and June have swim lessons on Saturday, Oct 17, 9:00 to 9:45 am "
        f"at the Westside YMCA [ep:{swim.episode_id}]. "
        f"Mara thinks weekday evenings work best for lessons [ep:{question.episode_id}]."
    )
    model = os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku")
    judge = ModelJudge(ModelClient(ClaudeCliBackend(model=model)))

    verdict = judge.judge(replace(request(answer), cited_episodes=(hotel, swim, question)))

    assert verdict.correct is True, verdict.reasoning
    assert set(verdict.relevant_citations) == {hotel.episode_id, swim.episode_id}
