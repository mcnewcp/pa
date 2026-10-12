"""The `query` tool's statement allowlist: one read-only Cypher statement, checked before it
reaches the database.

Opening the database read-only stops writes to the graph, but LadybugDB 0.21.2 still runs
statements with other effects on a read-only database (docs/research/ladybugdb-spike.md):
`COPY … TO` writes files, `INSTALL` downloads, `ATTACH` attaches, `LOAD FROM` reads local
files (or Python objects in the caller's frames), and a standalone `CALL <option>=…` changes
settings, such as lifting the query timeout. A string can also carry several statements.

So a statement must start with a read clause, contain no clause outside the read clauses, and
call only the read-only procedures in `PROCEDURES`. Keywords are found outside string literals,
comments, backquoted names, and property, label, and parameter names, so `'delete'` or
`n.name` is fine. The check is deliberately blunt: a write keyword used as a bare variable
name is rejected too, which a backquoted name avoids.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NoReturn

from pa_core.errors import PaError

READ_CLAUSES = ("MATCH", "OPTIONAL MATCH", "UNWIND", "WITH", "RETURN", "CALL")
"""What a statement may start with. WHERE, ORDER BY, SKIP, LIMIT, and UNION may follow."""

PROCEDURES = (
    "QUERY_FTS_INDEX",
    "QUERY_VECTOR_INDEX",
    "show_tables",
    "table_info",
    "show_connection",
    "show_indexes",
)
"""The procedures `CALL` may run: full-text and vector search, and schema listings."""

REJECTED = (
    "CREATE",
    "MERGE",
    "SET",
    "DELETE",
    "DETACH",
    "REMOVE",
    "DROP",
    "ALTER",
    "COPY",
    "LOAD",
    "INSTALL",
    "UNINSTALL",
    "UPDATE",
    "ATTACH",
    "USE",
    "IMPORT",
    "EXPORT",
    "CHECKPOINT",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
)
"""Keywords of the clauses that create, merge, set, delete, copy, load, install, attach,
import, export, or manage transactions."""

_FIRST_WORDS = {clause.split()[0] for clause in READ_CLAUSES}
_REJECTED = set(REJECTED)
_PROCEDURES = {name.upper() for name in PROCEDURES}

_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<line_comment>//[^\n]*)
    | (?P<block_comment>/\*.*?\*/)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<quoted>`(?:[^`]|``)*`)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


class RejectedQuery(PaError):
    """A `query` statement outside the allowlist; it never reached the database."""


def check_read_only(cypher: str) -> None:
    """Raise `RejectedQuery`, saying why, unless `cypher` is one allowlisted read statement."""
    tokens = _tokens(cypher)
    while tokens and tokens[-1].text == ";":
        tokens.pop()
    if not tokens:
        _reject("the statement is empty.")
    if any(token.text == ";" for token in tokens):
        _reject("one statement per call; remove the `;` between statements.")
    first = tokens[0].text.upper()
    if first not in _FIRST_WORDS:
        _reject(f"a statement must start with {', '.join(READ_CLAUSES)}; this starts {first}.")
    for index, token in enumerate(tokens):
        word = token.text.upper()
        if not token.word or _is_name(tokens, index):
            continue
        if word in _REJECTED:
            _reject(f"{word} is not allowed; `query` only reads.")
        if word == "CALL":
            called = tokens[index + 1].text if index + 1 < len(tokens) else ""
            follows = tokens[index + 2].text if index + 2 < len(tokens) else ""
            if called.upper() not in _PROCEDURES or follows != "(":
                _reject(f"CALL may only run {', '.join(PROCEDURES)}; settings can't be changed.")


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int
    word: bool


def _tokens(cypher: str) -> list[_Token]:
    """Words and punctuation, without whitespace, comments, or string literals' contents."""
    tokens: list[_Token] = []
    for match in _TOKEN.finditer(cypher):
        kind, text = match.lastgroup, match.group()
        if kind in {"space", "line_comment", "block_comment"}:
            continue
        if kind == "other" and text in {"'", '"', "`"}:
            _reject("a string or a backquoted name is never closed.")
        if kind == "other" and cypher.startswith("/*", match.start()):
            _reject("a comment is never closed.")
        shown = {"string": "'…'", "quoted": "`…`"}.get(kind or "", text)
        tokens.append(_Token(shown, match.start(), match.end(), word=kind == "word"))
    return tokens


def _is_name(tokens: list[_Token], index: int) -> bool:
    """A property, label, or parameter name, written right after its `.`, `:`, or `$`:
    `n.set`, `(n:Copy)`, `$load`."""
    if index < 2:
        return False
    sigil, owner, token = tokens[index - 1], tokens[index - 2], tokens[index]
    if sigil.end != token.start or sigil.text not in {".", ":", "$"}:
        return False
    if sigil.text == "$":
        return True
    # `.` and `:` follow what they belong to: a variable, a backquoted name, or a `(`, `[`.
    return owner.end == sigil.start and (owner.word or owner.text in {"`…`", "(", "["})


def _reject(reason: str) -> NoReturn:
    raise RejectedQuery(f"Rejected before reaching the database: {reason}")
