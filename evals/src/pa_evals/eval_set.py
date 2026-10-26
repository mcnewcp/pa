"""The eval set: the fixed questions each milestone is scored against.

Stored as JSON. The set names the owner the corpus belongs to, their time zone, and a default
as-of time; each question may override the as-of time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, ValidationError, field_validator, model_validator

from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_evals.models import FrozenModel
from pa_home.cut import Cut

type Category = Literal[
    "single_fact_recall",
    "attribution",
    "timeline_narrative",
    "stance_change",
    "open_loops",
    "cross_channel_synthesis",
    "entity_resolution",
    "connection",
    "paraphrase",
    "abstention",
    "where_things_stand",
    "canary",
]
"""What a question measures. A `paraphrase` question is worded unlike its evidence, so a lexical
search for its words misses. A `connection` question asks how people, organizations, and topics
relate, by paths no single lookup anticipates. A `where_things_stand` question asks for a topic's
current state, its decisions, and what's still open, or sweeps across topics for one of those
(the decisions still pending). A `canary` checks that the agent treats the as-of time as now."""

CATEGORIES: tuple[str, ...] = get_args(Category.__value__)


class EvalSetError(PaError):
    """An eval set file that is missing or does not match the format."""


class EvalOwner(FrozenModel):
    """The owner the corpus belongs to; ingest and the agent treat them as the owner."""

    name: str
    email_addresses: list[str] = Field(min_length=1)
    other_names: list[str] = []

    @classmethod
    def from_owner(cls, owner: Owner) -> EvalOwner:
        return cls(
            name=owner.name,
            email_addresses=list(owner.email_addresses),
            other_names=list(owner.other_names),
        )

    def to_owner(self) -> Owner:
        return Owner(
            name=self.name,
            email_addresses=tuple(self.email_addresses),
            other_names=tuple(self.other_names),
        )


class EvalQuestion(FrozenModel):
    id: str
    category: Category
    question: str
    expected_answer: str
    evidence: list[str] = []
    """The episode ids a good answer cites. Empty for questions with no evidence (abstention)."""
    as_of: AwareDatetime | None = None
    """Overrides the set's as-of time for this question."""


class EvalSet(FrozenModel):
    as_of: AwareDatetime
    """When the questions are asked from, unless a question overrides it."""
    owner: EvalOwner
    time_zone: str
    """The owner's IANA time zone, which the corpus's storylines are written in."""
    questions: list[EvalQuestion]
    adversarial: list[str] = []
    """The episode ids of the corpus's adversarial emails, which try to plant instructions or
    claims about the owner."""

    @field_validator("time_zone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"not a known IANA time zone: {value!r}") from error
        return value

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    @model_validator(mode="after")
    def _unique_ids(self) -> EvalSet:
        ids = [question.id for question in self.questions]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"question ids must be unique; repeated: {', '.join(duplicates)}")
        return self

    def as_of_for(self, question: EvalQuestion) -> AwareDatetime:
        return question.as_of or self.as_of

    def only(self, *ids: str) -> EvalSet:
        """The set narrowed to the questions with `ids`, kept in set order."""
        unknown = sorted(set(ids) - {question.id for question in self.questions})
        if unknown:
            raise EvalSetError(f"no such question in the eval set: {', '.join(unknown)}")
        return self.model_copy(update={"questions": [q for q in self.questions if q.id in ids]})


def load_eval_set(path: Path, *, cut: Cut | None = None) -> EvalSet:
    """The eval set in `path`. Over a `cut`, refused unless every episode it names (each
    question's evidence, and the adversarial episodes) is in the cut."""
    try:
        eval_set = EvalSet.model_validate_json(path.read_bytes())
    except FileNotFoundError as error:
        raise EvalSetError(f"eval set not found: {path}") from error
    except ValidationError as error:
        raise EvalSetError(f"eval set {path} does not match the format: {error}") from error
    if cut is not None:
        named = {i for q in eval_set.questions for i in q.evidence} | set(eval_set.adversarial)
        missing = sorted(named - cut.episode_ids())
        if missing:
            raise EvalSetError(
                f"eval set {path} names episodes that are not in the cut {cut.name}: "
                + ", ".join(missing)
            )
    return eval_set
