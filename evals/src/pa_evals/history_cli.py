"""`pa-eval wiki-history`: export a wiki's history to files, a file for each run, for a
diagnosis's case to hold (see `pa_evals.wiki_history`).

It runs on the devbox, where a wiki's git directory is: the live wiki's, for a cut's own wiki,
up to the commit the cut holds, or a dreamed copy's own. The history is real data: it is refused
inside the repository, and what the command prints is a count and the path.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.librarian.replay import ReplayTarget
from pa_evals.real_data import require_private
from pa_evals.wiki_history import WikiHistoryError, export_history
from pa_home.config import load_data_root
from pa_home.cut import load_cut


def add_wiki_history_command(commands: Any) -> None:
    command = commands.add_parser(
        "wiki-history",
        help="Export a wiki's history to files, a file for each run, for a diagnosis's case.",
    )
    wiki = command.add_mutually_exclusive_group(required=True)
    wiki.add_argument(
        "--cut",
        type=Path,
        metavar="DIR",
        help="A cut (`pa cut`): the history of its own wiki, from the data root's, up to the "
        "commit the cut holds.",
    )
    wiki.add_argument(
        "--copy",
        type=Path,
        metavar="DIR",
        help="A dreamed copy of a cut's wiki (`pa librarian dream DIR --cut CUT`): the history "
        "in the git directory beside its `wiki/`.",
    )
    command.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="DIR",
        help="Where the history is left: a new directory, for the owner alone.",
    )
    command.set_defaults(handler=_wiki_history)


def _wiki_history(args: argparse.Namespace) -> int:
    # Every path is checked before any is read.
    out = require_private(args.out, "--out")
    if args.copy is not None:
        git_dir = ReplayTarget(require_private(args.copy, "--copy")).git_dir
        if not git_dir.is_dir():
            raise WikiHistoryError(
                f"{args.copy} has no {git_dir.name}: it isn't a dreamed copy that has run"
            )
        until = None
    else:
        cut = load_cut(require_private(args.cut, "--cut"))
        if cut.manifest.wiki is None:
            raise WikiHistoryError(f"the cut {cut.name} holds no wiki, so it has no history")
        git_dir, until = load_data_root().wiki_git, cut.manifest.wiki.commit
    runs = export_history(git_dir, out, until=until)
    print(f"history: {runs} runs")
    print(f"left: {out}")
    return 0
