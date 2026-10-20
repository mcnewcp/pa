"""Evidence citations in an answer: `[ep:<episode_id>]`, and which of them the session opened."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

from pa_core.envelope import Envelope
from pa_evals.agent import ToolCall

_CITATION = re.compile(r"\[ep:\s*([A-Za-z0-9_-]+)\s*\]")

RUN = 10
"""How many consecutive words of an episode's body a tool's output must show for the
episode's contents to have reached the session."""

_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})|\\[nrtbf]")
_WORD = re.compile(r"\w+")


def parse_citations(answer: str) -> list[str]:
    """The episode ids cited in `answer`, each once, in the order first cited."""
    return list(dict.fromkeys(_CITATION.findall(answer)))


def opened(
    cited: Iterable[str], tool_calls: Iterable[ToolCall], episodes: Mapping[str, Envelope]
) -> list[str]:
    """The cited episode ids whose contents reached the session, in citation order.

    `episodes` are the cited episodes that exist, by id. An episode counts as opened when:

    - a Read of one of its files (its envelope, `<episode_id>.json`, or its raw payload,
      `<episode_id>.<extension>`, wherever L0 is) didn't error; or
    - any tool's output (a Bash command's, say, however it found the file) shows its
      contents: `RUN` consecutive words of its body, or, for a body shorter than that, the
      whole body and the whole subject. Words are compared without case or punctuation, and
      JSON escapes are read as what they stand for, so a body printed as text and one shown
      inside its envelope's JSON both count.

    Naming an episode's id or path, in a command or in its output (a list of files grep
    matched, say), doesn't count, nor does showing its subject alone.
    """
    calls = list(tool_calls)
    read = {
        PurePosixPath(str(call.input.get("file_path", ""))).name
        for call in calls
        if call.tool == "Read" and not call.errored
    }
    outputs = [_words(call.output) for call in calls if call.output]
    shown = [(f" {' '.join(words)} ", _runs(words)) for words in outputs]

    def is_opened(episode_id: str) -> bool:
        if any(_is_file_of(episode_id, name) for name in read):
            return True
        episode = episodes.get(episode_id)
        return episode is not None and any(_shows(episode, *output) for output in shown)

    return [episode_id for episode_id in cited if is_opened(episode_id)]


def _is_file_of(episode_id: str, name: str) -> bool:
    """Whether `name` is the episode's envelope or raw payload: its id and an extension."""
    stem, dot, extension = name.partition(".")
    return stem == episode_id and bool(dot and extension)


def _shows(episode: Envelope, text: str, runs: set[tuple[str, ...]]) -> bool:
    """Whether an output, as its words (`text`) and their runs, shows the episode's contents."""
    body = _words(episode.body)
    if len(body) >= RUN:
        return not runs.isdisjoint(_runs(body))
    subject = _words(episode.subject or "")
    return bool(body or subject) and all(
        not words or f" {' '.join(words)} " in text for words in (body, subject)
    )


def _runs(words: list[str]) -> set[tuple[str, ...]]:
    """Every `RUN` consecutive words in `words`."""
    return {tuple(words[i : i + RUN]) for i in range(len(words) - RUN + 1)}


def _words(text: str) -> list[str]:
    """`text`'s words, lowercased, with JSON escapes read as what they stand for."""
    unescaped = _ESCAPE.sub(lambda m: chr(int(m[1], 16)) if m[1] else " ", text)
    return _WORD.findall(unescaped.lower())
