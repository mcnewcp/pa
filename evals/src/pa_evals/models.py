"""The pydantic base shared by the evals' file formats (storyline spec, questions, eval set)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError


class FrozenModel(BaseModel):
    """Immutable once read, and strict: a field the format does not know is an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def problems_of(error: ValidationError) -> tuple[str, ...]:
    """Where a file or a reply fails its format and why, a line for each place, never quoting
    what it holds. A place can still be a field's name, which whoever wrote the file chose."""
    return tuple(
        f"{'.'.join(str(part) for part in problem['loc']) or 'the whole'}: {problem['msg']}"
        for problem in error.errors(include_input=False, include_url=False)
    )
