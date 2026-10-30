"""Drafting the real-data eval set: a session reads a cut's L0 and drafts the set, for the owner
to correct.

The session reads a copy of the cut's L0 laid read-only in a throwaway data root, and never the
cut's wiki, so the questions don't lean on what the librarian already extracted. It is asked at
the cut's as-of time, and its reply is the draft's questions as JSON. The questions are numbered
here, with opaque ids (`r01`, `r02`, ...), so a public score never hints at what one asked; and a
draft naming any episode the cut doesn't hold is refused whole.

A second pass revises a draft (as the owner left it) by the owner's notes. It keeps the ids of
the questions it keeps, and numbers new ones after the last.

The prompts are versioned in `draft_prompts/`, since they hold nothing private. Nothing here
ever puts a question, an answer, or the session's reply in an error: errors say where the reply
went wrong, and name only episode ids, which are opaque hashes.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from pa_core.envelope import Source
from pa_core.errors import PaError
from pa_core.owner import Owner
from pa_core.status import read_status
from pa_evals.eval_set import (
    CATEGORIES,
    Category,
    EvalOwner,
    EvalQuestion,
    EvalSet,
    format_problems,
)
from pa_evals.harness import lay_cut
from pa_home.agent import ClaudeAgent
from pa_home.config import DataRoot
from pa_home.cut import Cut

PROMPTS = Path(__file__).parent / "draft_prompts"
DRAFT_PROMPT = PROMPTS / "draft.md"
"""The first pass's prompt."""
REVISE_PROMPT = PROMPTS / "revise.md"
"""The second pass's prompt, which applies the owner's notes."""

DEFAULT_QUESTIONS = 25
"""About how many questions the first pass drafts."""

QUESTION_ID = re.compile(r"r\d{2,}")
"""An opaque question id: `r` and a number, at least two digits."""


class DraftError(PaError):
    """A draft that can't be made, or a reply that isn't one. Its message never quotes the
    reply."""


@dataclass(frozen=True)
class DraftRequest:
    """What a drafting session is given."""

    prompt: str
    owner: Owner
    l0: Path
    """The cut's L0, laid read-only in a throwaway data root. Never a wiki."""
    as_of: datetime
    """The session's "now": the cut's as-of time, in the owner's time zone."""


class Drafter(Protocol):
    def draft(self, request: DraftRequest) -> str:
        """The session's reply: the draft's questions as JSON, perhaps in a code fence."""
        ...


class ClaudeDrafter:
    """A `claude -p` session in the agent project, without the wiki and with exchange capture
    off, as `ClaudeAgent` runs one."""

    def __init__(self, agent: ClaudeAgent) -> None:
        self._agent = agent

    def draft(self, request: DraftRequest) -> str:
        answer = self._agent.answer(
            request.prompt, owner=request.owner, l0=request.l0, wiki=None, now=request.as_of
        )
        return answer.text


class ScriptedDrafter:
    """A fake drafter that replies with `reply`, and records every request in `requests`."""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.requests: list[DraftRequest] = []

    def draft(self, request: DraftRequest) -> str:
        self.requests.append(request)
        return self._reply


def draft_eval_set(
    cut: Cut,
    drafter: Drafter,
    *,
    owner: Owner,
    time_zone: str,
    questions: int = DEFAULT_QUESTIONS,
) -> EvalSet:
    """The first pass: a draft eval set over `cut`, asked at its as-of time."""
    zone = ZoneInfo(time_zone)
    template = Template(DRAFT_PROMPT.read_text())
    prompt = template.substitute(_about(cut, zone), questions=questions)
    drafted = _ask(cut, drafter, prompt, owner, zone)
    eval_set = EvalSet(
        as_of=cut.as_of.astimezone(zone),
        owner=EvalOwner.from_owner(owner),
        time_zone=time_zone,
        questions=_numbered(drafted, keep=set()),
    )
    _require_in_cut(eval_set, cut)
    return eval_set


def revise_eval_set(cut: Cut, drafter: Drafter, *, previous: EvalSet, notes: str) -> EvalSet:
    """The second pass: `previous`, as the owner left it, revised by their `notes`."""
    template = Template(REVISE_PROMPT.read_text())
    prompt = template.substitute(
        _about(cut, previous.zone),
        draft=previous.model_dump_json(indent=2, include={"questions"}),
        notes=notes.strip() or "(no notes)",
    )
    revised = _ask(cut, drafter, prompt, previous.owner.to_owner(), previous.zone)
    kept = {question.id for question in previous.questions}
    eval_set = previous.model_copy(update={"questions": _numbered(revised, keep=kept)})
    _require_in_cut(eval_set, cut)
    return eval_set


def _about(cut: Cut, zone: ZoneInfo) -> dict[str, str]:
    """What both prompts say about the cut: its time, sources, and watermarks."""
    sources = sorted(Source(p.name) for p in (cut.l0 / "episodes").glob("*") if p.is_dir())
    recorded = read_status(cut.status).sources if cut.status is not None else {}
    watermarks = [
        f"  - {source}: {_when(recorded[source].watermark if source in recorded else None, zone)}"
        for source in sources
    ]
    return {
        "as_of": _when(cut.as_of, zone),
        "time_zone": zone.key,
        "sources": ", ".join(sources) or "(none)",
        "watermarks": "\n".join(watermarks) or "  - (no sources)",
        "categories": ", ".join(f"`{category}`" for category in CATEGORIES),
    }


def _when(moment: datetime | None, zone: ZoneInfo) -> str:
    return "never collected" if moment is None else moment.astimezone(zone).isoformat()


def _ask(
    cut: Cut, drafter: Drafter, prompt: str, owner: Owner, zone: ZoneInfo
) -> list[_DraftQuestion]:
    """The questions the drafter replies with, asked over a read-only copy of the cut's L0."""
    with tempfile.TemporaryDirectory(prefix="pa-draft-") as directory:
        data_root = DataRoot(Path(directory))
        lay_cut(cut, data_root)
        reply = drafter.draft(
            DraftRequest(
                prompt=prompt, owner=owner, l0=data_root.l0, as_of=cut.as_of.astimezone(zone)
            )
        )
    return _parsed(reply)


class _DraftQuestion(BaseModel):
    """A question as a reply gives it: its id only when it keeps one."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str | None = None
    category: Category
    question: str
    expected_answer: str
    evidence: list[str] = []
    as_of: AwareDatetime | None = None


class _DraftReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    questions: list[_DraftQuestion]


_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _parsed(reply: str) -> list[_DraftQuestion]:
    """The questions in `reply`: its last fenced block, or the reply itself, from its first `{`
    to its last `}`."""
    fenced = _FENCED.findall(reply)
    text = fenced[-1] if fenced else reply
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise DraftError("the session's reply holds no JSON object")
    try:
        parsed = _DraftReply.model_validate_json(text[start : end + 1])
    except ValidationError as error:
        raise DraftError(f"the session's reply isn't a draft: {format_problems(error)}") from None
    if not parsed.questions:
        raise DraftError("the session's reply drafts no questions")
    return parsed.questions


def _numbered(drafted: list[_DraftQuestion], *, keep: set[str]) -> list[EvalQuestion]:
    """The questions with opaque ids. A question keeps an opaque id in `keep` (the first to
    give it does); every other is numbered after the highest id in `keep`, so no id is ever
    reused."""
    following = max((int(i[1:]) for i in keep if QUESTION_ID.fullmatch(i)), default=0) + 1
    numbered: list[EvalQuestion] = []
    used: set[str] = set()
    for question in drafted:
        question_id = question.id
        if (
            question_id is None
            or question_id not in keep - used
            or not QUESTION_ID.fullmatch(question_id)
        ):
            question_id = f"r{following:02}"
            following += 1
        used.add(question_id)
        numbered.append(
            EvalQuestion(
                id=question_id,
                category=question.category,
                question=question.question,
                expected_answer=question.expected_answer,
                evidence=question.evidence,
                as_of=question.as_of,
            )
        )
    return numbered


def _require_in_cut(eval_set: EvalSet, cut: Cut) -> None:
    named = {i for question in eval_set.questions for i in question.evidence}
    missing = sorted(named - cut.episode_ids())
    if missing:
        raise DraftError(
            f"the draft names episodes that are not in the cut {cut.name}, so it was thrown "
            f"away: {', '.join(missing)}"
        )


def draft_json(eval_set: EvalSet) -> bytes:
    """The draft as it's written for the owner to edit: indented, one field to a line."""
    return (eval_set.model_dump_json(indent=2, exclude_none=True) + "\n").encode()


def evidence_count(eval_set: EvalSet) -> int:
    """How many distinct episodes the draft names."""
    return len({i for question in eval_set.questions for i in question.evidence})
