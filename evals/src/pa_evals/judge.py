"""The judge interface: how the harness scores one answer.

The judge decides correctness against the expected answer and which cited episodes are relevant
to the answer. Evidence recall and whether a cited episode exists are worked out by the harness,
not the judge. Anything a judge raises is recorded as a failed result for that question.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from pa_core.envelope import Envelope
from pa_core.errors import PaError
from pa_core.model_client import ModelClient


class JudgeError(PaError):
    """The judge could not score an answer."""


class Verdict(BaseModel):
    """The judge's decision on one answer."""

    correct: bool = Field(description="Whether the answer is correct against the expected answer.")
    reasoning: str = Field(description="A short explanation of the decision.")
    relevant_citations: list[str] = Field(
        description="The ids of the cited episodes that support the claims they are cited for."
    )


@dataclass(frozen=True)
class JudgeRequest:
    question_id: str
    question: str
    expected_answer: str
    as_of: datetime
    answer: str
    cited_episodes: tuple[Envelope, ...]
    """The cited episodes that exist in L0, in citation order. Unknown ids are left out."""


class Judge(Protocol):
    def judge(self, request: JudgeRequest) -> Verdict: ...


class ModelJudge:
    """A judge that asks a model, through the model client, to score the answer with a rubric."""

    def __init__(self, client: ModelClient) -> None:
        self._client = client

    def judge(self, request: JudgeRequest) -> Verdict:
        return self._client.generate(judge_prompt(request), Verdict)


_RUBRIC = """\
You are grading an answer from a personal assistant. Its owner asked the question below at the
as-of time, which the assistant treats as "now". Grade it against the expected answer.

Rubric for `correct`:
- true when the answer states the facts of the expected answer, in any wording. Extra detail is
  fine unless it contradicts the expected answer.
- false when it misses a key fact of the expected answer, gets one wrong, or answers a different
  question.
- When the expected answer says the information is not known, the answer is correct only if it
  says it does not know (or cannot find it) instead of inventing an answer.

For `relevant_citations`, check each citation separately from `correct`. The answer cites an
episode as [ep:<id>] next to the claim it backs. List the id when the episode's content supports
the claim it is cited for, even when that claim goes beyond the expected answer or the question:
extra detail that is accurately cited is still a relevant citation. Leave the id out when the
episode does not support the claim it is cited for, or has nothing to do with the answer.

Keep `reasoning` to a few sentences."""


def judge_prompt(request: JudgeRequest) -> str:
    """The prompt `ModelJudge` sends for `request`."""
    if request.cited_episodes:
        episodes = "\n\n".join(_render_episode(e) for e in request.cited_episodes)
    else:
        episodes = "(The answer cites no episodes that exist.)"
    return (
        f"{_RUBRIC}\n\n"
        f"As-of time: {request.as_of.isoformat()}\n\n"
        f"Question:\n{request.question}\n\n"
        f"Expected answer:\n{request.expected_answer}\n\n"
        f"Answer to grade:\n{request.answer}\n\n"
        f"Cited episodes:\n\n{episodes}"
    )


def _render_episode(envelope: Envelope) -> str:
    when = envelope.occurred_at.start.isoformat()
    if envelope.occurred_at.end is not None:
        when += f" to {envelope.occurred_at.end.isoformat()}"
    lines = [
        f"--- Episode {envelope.episode_id} ({envelope.source}, {envelope.kind})",
        f"When: {when}",
    ]
    if envelope.calendar_name:
        lines.append(f"Calendar: {envelope.calendar_name}")
    for participant in envelope.participants:
        name = f"{participant.name} " if participant.name else ""
        lines.append(f"{participant.role.capitalize()}: {name}<{participant.identifier}>")
    if envelope.subject:
        lines.append(f"Subject: {envelope.subject}")
    lines += ["", envelope.body]
    return "\n".join(lines)


class ScriptedJudge:
    """A fake judge that returns a scripted verdict per question id.

    Every request it receives is recorded in `requests`.
    """

    def __init__(self, verdicts: Mapping[str, Verdict]) -> None:
        self._verdicts = dict(verdicts)
        self.requests: list[JudgeRequest] = []

    def judge(self, request: JudgeRequest) -> Verdict:
        self.requests.append(request)
        verdict = self._verdicts.get(request.question_id)
        if verdict is None:
            raise JudgeError(f"no scripted verdict for question {request.question_id}")
        return verdict
