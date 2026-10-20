"""Evidence citations in an answer: `[ep:<episode_id>]`."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from pa_evals.agent import ToolCall

_CITATION = re.compile(r"\[ep:\s*([A-Za-z0-9_-]+)\s*\]")


def parse_citations(answer: str) -> list[str]:
    """The episode ids cited in `answer`, each once, in the order first cited."""
    return list(dict.fromkeys(_CITATION.findall(answer)))


def opened(cited: Iterable[str], tool_calls: Iterable[ToolCall]) -> list[str]:
    """The cited episode ids whose file the session read, in citation order.

    An episode counts as opened when a Read of its file (`<episode_id>.json`, wherever L0 is)
    succeeded. Finding its id with a search doesn't count.
    """
    read = {
        PurePosixPath(str(call.input.get("file_path", ""))).name
        for call in tool_calls
        if call.tool == "Read" and not call.errored
    }
    return [episode_id for episode_id in cited if f"{episode_id}.json" in read]
