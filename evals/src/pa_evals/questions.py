"""The eval set's source: questions written against the storyline spec.

Questions are hand-written in YAML with their evidence named by spec event id, never by
episode id. `build_eval_set` turns them into the eval set the harness reads (`EvalSet`, stored
as JSON), working out each episode id from the generated corpus with ingest's own rules and
taking the owner from the spec. So the evidence ids are always right, and a regenerated corpus
only needs a rebuild.

```yaml
as_of: 2026-10-14T20:00:00-05:00      # the default as-of time
questions:
  - id: hotel-block-deadline
    category: single_fact_recall      # one of eval_set.CATEGORIES
    question: When does the hotel block close?
    expected_answer: October 9.
    evidence: [wedding-hotel-block]   # spec event ids; leave out for none
    as_of: 2026-10-10T09:00:00-05:00  # optional override
```
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from pa_core.errors import PaError
from pa_evals.corpus import evidence_ids
from pa_evals.eval_set import EvalOwner, EvalSet
from pa_evals.storyline import StorylineSpec


class QuestionsError(PaError):
    """A questions file that cannot be read or does not fit the spec."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceQuestion(_Model):
    id: str
    category: str
    question: str
    expected_answer: str
    evidence: list[str] = []
    """Spec event ids."""
    as_of: AwareDatetime | None = None


class Questions(_Model):
    as_of: AwareDatetime
    questions: list[SourceQuestion]


def load_questions(path: Path) -> Questions:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise QuestionsError(f"cannot read questions {path}: {error}") from None
    try:
        return Questions.model_validate(data)
    except ValidationError as error:
        raise QuestionsError(f"questions {path} are invalid:\n{error}") from None


def build_eval_set(questions_path: Path, spec: StorylineSpec, corpus_dir: Path) -> EvalSet:
    """The eval set for the questions in `questions_path`, over the corpus generated from `spec`."""
    questions = load_questions(questions_path)
    unknown = [
        f"{question.id}: {event_id}"
        for question in questions.questions
        for event_id in question.evidence
        if spec.find_event(event_id) is None
    ]
    if unknown:
        raise QuestionsError(f"evidence names events not in the spec: {'; '.join(unknown)}")
    ids = evidence_ids(spec, corpus_dir)
    owner = spec.owner_identity
    data = {
        "as_of": questions.as_of,
        "owner": EvalOwner(
            name=owner.name,
            email_addresses=list(owner.email_addresses),
            other_names=list(owner.other_names),
        ),
        "questions": [
            {**question.model_dump(), "evidence": [ids[event] for event in question.evidence]}
            for question in questions.questions
        ],
    }
    try:
        return EvalSet.model_validate(data)
    except ValidationError as error:
        raise QuestionsError(
            f"questions {questions_path} do not make a valid eval set:\n{error}"
        ) from None
