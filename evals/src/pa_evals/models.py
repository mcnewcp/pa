"""The pydantic base shared by the evals' file formats (storyline spec, questions, eval set)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable once read, and strict: a field the format does not know is an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")
