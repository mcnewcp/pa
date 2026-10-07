"""Evidence citations in an answer: `[ep:<episode_id>]`."""

from __future__ import annotations

import re

_CITATION = re.compile(r"\[ep:\s*([A-Za-z0-9_-]+)\s*\]")


def parse_citations(answer: str) -> list[str]:
    """The episode ids cited in `answer`, each once, in the order first cited."""
    return list(dict.fromkeys(_CITATION.findall(answer)))
