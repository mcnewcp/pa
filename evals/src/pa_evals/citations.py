"""Evidence citations in an answer: `[ep:<episode_id>]`, and which of them the session opened."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath

from pa_evals.agent import ToolCall

_CITATION = re.compile(r"\[ep:\s*([A-Za-z0-9_-]+)\s*\]")

READERS = frozenset(
    {"cat", "head", "tail", "less", "more", "jq", "sed", "awk", "grep", "egrep", "rg", "nl", "bat"}
)
"""The shell commands that put a file they're given into the session's output."""

_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "(", ")"})


def parse_citations(answer: str) -> list[str]:
    """The episode ids cited in `answer`, each once, in the order first cited."""
    return list(dict.fromkeys(_CITATION.findall(answer)))


def opened(cited: Iterable[str], tool_calls: Iterable[ToolCall]) -> list[str]:
    """The cited episode ids whose file the session read, in citation order.

    An episode's files are its envelope, `<episode_id>.json`, and its raw payload,
    `<episode_id>.<extension>`, wherever L0 is. It counts as opened when a call that didn't
    error read one of them:

    - a Read of the file;
    - a Bash command in which one of `READERS` (cat, jq, head, sed, less, grep, ...) names the
      file as an argument. A grep of that one named file counts; a grep over a directory that
      merely matches the episode doesn't, nor does a command that only lists or finds the file
      (ls, find).

    Finding an id with a search (Grep, Glob, or a recursive grep) never counts.
    """
    names: set[str] = set()
    for call in tool_calls:
        if call.errored:
            continue
        if call.tool == "Read":
            names.add(PurePosixPath(str(call.input.get("file_path", ""))).name)
        elif call.tool == "Bash":
            names.update(_read_by(str(call.input.get("command", ""))))
    return [episode_id for episode_id in cited if any(_is_file_of(episode_id, n) for n in names)]


def _is_file_of(episode_id: str, name: str) -> bool:
    """Whether `name` is the episode's envelope or raw payload: its id and an extension."""
    stem, dot, extension = name.partition(".")
    return stem == episode_id and bool(dot and extension)


def _read_by(command: str) -> Iterator[str]:
    """The names of the files a reader in `command` is given as arguments."""
    for segment in _segments(command):
        if segment and PurePosixPath(segment[0]).name in READERS:
            for word in segment[1:]:
                if not word.startswith("-"):
                    yield PurePosixPath(word).name


def _segments(command: str) -> list[list[str]]:
    """`command`'s simple commands, as words, split at pipes, `;`, `&&` and `||`."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:  # unbalanced quotes: fall back to whitespace
        words = command.split()
    segments: list[list[str]] = [[]]
    for word in words:
        if word in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(word)
    return segments
