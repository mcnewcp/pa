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

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import AwareDatetime, ValidationError

from pa_core.errors import PaError
from pa_evals.corpus import evidence_ids
from pa_evals.eval_set import Category, EvalOwner, EvalSet
from pa_evals.models import FrozenModel
from pa_evals.storyline import StorylineSpec


class QuestionsError(PaError):
    """A questions file that cannot be read or does not fit the spec."""


class SourceQuestion(FrozenModel):
    id: str
    category: Category
    question: str
    expected_answer: str
    evidence: list[str] = []
    """Spec event ids."""
    as_of: AwareDatetime | None = None


class Questions(FrozenModel):
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


def build_eval_set(
    questions_paths: Sequence[Path], spec: StorylineSpec, corpus_dirs: Sequence[Path]
) -> EvalSet:
    """The eval set for the questions in `questions_paths`, over the corpus generated from
    `spec`, which is in `corpus_dirs` (for an extended spec, the base corpus and the
    extension's). The questions files must share one as-of time; their questions keep their
    order. The set lists the episodes of the spec's adversarial emails too."""
    files = [load_questions(path) for path in questions_paths]
    if not files:
        raise QuestionsError("no questions files given")
    as_of = files[0].as_of
    differing = [str(p) for p, f in zip(questions_paths, files, strict=True) if f.as_of != as_of]
    if differing:
        raise QuestionsError(
            f"questions files must share one as-of time ({as_of.isoformat()} in "
            f"{questions_paths[0]}); these differ: {', '.join(differing)}"
        )
    questions = [question for file in files for question in file.questions]
    unknown = [
        f"{question.id}: {event_id}"
        for question in questions
        for event_id in question.evidence
        if spec.find_event(event_id) is None
    ]
    if unknown:
        raise QuestionsError(f"evidence names events not in the spec: {'; '.join(unknown)}")
    ids = evidence_ids(spec, *corpus_dirs)
    data = {
        "as_of": as_of,
        "owner": EvalOwner.from_owner(spec.owner_identity),
        "time_zone": spec.timezone,
        "questions": [
            {**question.model_dump(), "evidence": [ids[event] for event in question.evidence]}
            for question in questions
        ],
        "adversarial": [ids[event] for event in spec.adversarial_ids],
    }
    try:
        return EvalSet.model_validate(data)
    except ValidationError as error:
        names = ", ".join(str(path) for path in questions_paths)
        raise QuestionsError(f"questions {names} do not make a valid eval set:\n{error}") from None
