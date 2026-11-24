"""`pa-eval reproduction resolve`: resolve a released reproduction again after an edit (see
`pa_evals.reproduction.resolve_again`).

A development session reads a released reproduction before it commits it, and renames or trims
it where it reads like a real matter. An edited payload can ingest to another episode, whose id
the questions' evidence and the wiki's citations don't name. This puts them right, in place.

It reads only a reproduction that was released, which a development session may read, so what
it prints may say what is wrong with one and where.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pa_evals.reproduction import UnresolvedError, resolve_again


def add_reproduction_command(commands: Any) -> None:
    reproduction = commands.add_parser("reproduction", help="Work on a released reproduction.")
    reproduction_commands = reproduction.add_subparsers(dest="reproduction_command", required=True)
    resolve = reproduction_commands.add_parser(
        "resolve",
        help="Resolve a released reproduction's questions and wiki again, after its payloads "
        "were edited.",
        description="Resolve a released reproduction again, in place: its questions' evidence "
        "and its wiki's citations, to the episodes its payloads ingest to now. Run it on a "
        "copy of a released reproduction after renaming or trimming it, and before committing "
        "it.",
    )
    resolve.add_argument(
        "directory",
        type=Path,
        metavar="DIR",
        help="The reproduction: a copy of `<released>/<diagnosis>/reproductions/<finding>`.",
    )
    resolve.set_defaults(handler=_resolve)


def _resolve(args: argparse.Namespace) -> int:
    try:
        resolved = resolve_again(args.directory)
    except UnresolvedError as error:
        for flaw in error.flaws:
            print(f"{flaw.where}: {flaw.what}", file=sys.stderr)
        raise
    record = resolved.record
    print(f"reproduction: {record.finding}, resolved again")
    print(f"payloads: {record.payloads} ({resolved.renewed} with a new episode id)")
    print(f"questions: {record.questions}")
    print(f"wiki files: {record.wiki_files}")
    return 0
