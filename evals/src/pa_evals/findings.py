"""The findings schema: what a diagnosis tells a development session (ADR-0010).

Findings say where the fault lies and how it happens, in terms of the PA's code and
instructions. They name questions and episodes only by their opaque ids, and their free text is
capped, so there is little room for anything but the mechanism. The caps are constants, not
configuration.

A session's `findings.json` is read through `parse_findings`, which holds it to the schema. What
is wrong with one is private: a problem's place can be a field's name, which the session chose.
So `FindingsError` says how many problems there are, and carries each for the private notes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, StringConstraints, ValidationError, model_validator

from pa_core.envelope import Source
from pa_core.errors import PaError
from pa_evals.models import FrozenModel

SUMMARY_CHARACTERS = 600
"""The most characters the findings' summary may hold."""
TEXT_CHARACTERS = 800
"""The most characters a finding's mechanism, and its suggestion, may each hold."""
MOST_FINDINGS = 10
"""The most findings a diagnosis may leave."""
PATH_CHARACTERS = 200
"""The most characters a repository path may hold."""

Answered = Literal["fully", "partly", "no"]
"""Whether the referral was answered."""
Kind = Literal["fault", "noise", "eval-set-defect", "undetermined"]
"""What a finding is: a fault in the PA, run-to-run noise, a defect in the eval set, or a
question the diagnostician couldn't settle."""
Component = Literal[
    "ingest",
    "observation",
    "consolidation",
    "dreaming",
    "candidates",
    "lint",
    "navigation",
    "answer",
    "judge",
    "eval-set",
    "harness",
]
"""Where a finding's fault lies: capture and ingest, the librarian's observation,
consolidation, or dreaming, the host's candidates, lint, the assistant's navigation or its
answer, the judge, the eval set, or the harness."""
Confidence = Literal["low", "medium", "high"]

FindingId = Annotated[str, StringConstraints(pattern=r"^f[1-9]\d?$")]
"""A finding's id: `f1`, `f2`, ..."""
QuestionId = Annotated[str, StringConstraints(pattern=r"^r\d{2,4}$")]
"""An opaque question id, as a real-data eval set numbers its questions (`r07`)."""
EpisodeId = Annotated[str, StringConstraints(pattern=rf"^({'|'.join(Source)})_[0-9a-f]{{16}}$")]
"""An episode id: its source, then 16 hex characters."""
LABEL = r"[a-z0-9][a-z0-9-]{0,31}"
"""What a label is made of."""
Label = Annotated[str, StringConstraints(pattern=f"^{LABEL}$")]
"""A label the referral gave a set of runs, or a wiki: lowercase letters, digits, and hyphens."""


class Finding(FrozenModel):
    """One thing a diagnosis found."""

    id: FindingId
    kind: Kind
    component: Component
    questions: list[QuestionId]
    """The questions it explains, by opaque id."""
    episodes: list[EpisodeId]
    """The episodes involved, by id."""
    runs: dict[Label, tuple[NonNegativeInt, NonNegativeInt]]
    """Under each label the referral gave: the runs affected, of those asked."""
    mechanism: str = Field(min_length=1, max_length=TEXT_CHARACTERS)
    """How it happens, in terms of the code and instructions."""
    code: list[Annotated[str, StringConstraints(min_length=1, max_length=PATH_CHARACTERS)]]
    """The repository paths it blames, relative to the repository's root."""
    suggestion: str = Field(max_length=TEXT_CHARACTERS)
    """A suggested fix ("" for none)."""
    confidence: Confidence

    @model_validator(mode="after")
    def _no_more_runs_affected_than_asked(self) -> Finding:
        if any(affected > asked for affected, asked in self.runs.values()):
            raise ValueError("a label counts more runs affected than asked")
        return self


class Findings(FrozenModel):
    """A diagnosis's findings."""

    answered: Answered
    summary: str = Field(min_length=1, max_length=SUMMARY_CHARACTERS)
    findings: list[Finding] = Field(max_length=MOST_FINDINGS)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Findings:
        ids = [finding.id for finding in self.findings]
        if len(set(ids)) != len(ids):
            raise ValueError("two findings share an id")
        return self

    def kinds(self) -> dict[str, int]:
        """How many findings there are of each kind that has any."""
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return counts


class FindingsError(PaError):
    """Findings off the schema. Its message says how many problems there are; `problems` says
    where each is and why, which is private, since a place can be a name the session chose."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__(f"the findings don't fit the schema ({len(problems)} problems)")
        self.problems = problems


def parse_findings(data: bytes) -> Findings:
    """The findings in `data`, a `findings.json`'s bytes; raises FindingsError unless they fit
    the schema."""
    try:
        return Findings.model_validate_json(data)
    except ValidationError as error:
        raise FindingsError(
            tuple(
                f"{'.'.join(str(part) for part in problem['loc']) or 'the whole'}: {problem['msg']}"
                for problem in error.errors(include_input=False, include_url=False)
            )
        ) from None


def findings_json(findings: Findings) -> bytes:
    """The findings as they are released: the schema's fields and nothing else, indented."""
    return (findings.model_dump_json(indent=2) + "\n").encode()
