"""`pa-eval reproduction`: work on a released reproduction.

`resolve` resolves one again after an edit (see `pa_evals.reproduction.resolve_again`). A
development session reads a released reproduction before it commits it, and renames or trims
it where it reads like a real matter. An edited payload can ingest to another episode, whose id
the questions' evidence and the wiki's citations don't name. This puts them right, in place.

`dream` dreams a copy of one's starting wiki, for a fault in dreaming: over its payloads, as
its eval set's owner, as of its eval set's as-of time. `pa-eval run --reproduction DIR --wiki
COPY` then asks its questions with the dreamed copy. The home adapter runs the dreaming
(`pa_home.librarian.dream_copy`) and knows nothing of a reproduction's layout (ADR-0010), so
the command is here.

Both read only a reproduction that was released, which a development session may read, so what
they print may say what is wrong with one and where.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from pa_core.errors import PaError
from pa_core.librarian.replay import ReplayTarget
from pa_evals.eval_set import load_eval_set
from pa_evals.reproduction import UnresolvedError, read_released, resolve_again
from pa_home import librarian
from pa_home.config import load_librarian_config, load_time_zone


class ReproductionCopyError(PaError):
    """A reproduction with no starting wiki to dream, or a directory that can't hold a copy of
    it."""


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
    dream = reproduction_commands.add_parser(
        "dream",
        help="Dream a copy of a released reproduction's starting wiki.",
        description="Run dreaming once over a copy of a released reproduction's starting wiki, "
        "over its payloads, as its eval set's owner and as of its eval set's as-of time. The "
        "first run makes the copy; a later run dreams the copy as it stands. The time zone "
        "(PA_TIME_ZONE) and the librarian's configuration come from the environment, as they "
        "do for `pa librarian dream`. Ask its questions with the copy afterwards: "
        "`pa-eval run --reproduction DIR --configuration files-plus-wiki --wiki COPY`.",
    )
    dream.add_argument(
        "directory",
        type=Path,
        metavar="DIR",
        help="The reproduction: `<released>/<diagnosis>/reproductions/<finding>`, or a copy.",
    )
    dream.add_argument(
        "copy",
        type=Path,
        metavar="COPY",
        help="Where the copy is kept, laid out as a replay's target is: wiki/, its git "
        "directory, and run-log.jsonl.",
    )
    dream.set_defaults(handler=_dream)


def _resolve(args: argparse.Namespace) -> int:
    try:
        resolved = resolve_again(args.directory)
    except UnresolvedError as error:
        for problem in error.problems:
            print(f"{problem.where}: {problem.what}", file=sys.stderr)
        raise
    record = resolved.record
    print(f"reproduction: {record.finding}, resolved again")
    print(f"payloads: {record.payloads} ({resolved.renewed} with a new episode id)")
    print(f"questions: {record.questions}")
    print(f"wiki files: {record.wiki_files}")
    return 0


def _dream(args: argparse.Namespace) -> int:
    reproduction = read_released(args.directory.expanduser().resolve())
    if reproduction.wiki is None:
        raise ReproductionCopyError(
            f"the reproduction {reproduction.finding} holds no starting wiki, so there is "
            "nothing to dream"
        )
    eval_set = load_eval_set(reproduction.eval_set)
    time_zone, config = load_time_zone(), load_librarian_config()
    copy = args.copy.expanduser().resolve()
    layout = ReplayTarget(copy)
    if not layout.tree.exists():
        if layout.run_log.exists() or layout.git_dir.exists():
            raise ReproductionCopyError(
                f"{copy} has no {layout.tree.name}/, but holds what a dreaming run leaves "
                "beside one: it isn't a copy this command can start"
            )
        # Nothing is copied through a link: the copy goes to the librarian's sessions.
        shutil.copytree(reproduction.wiki, layout.tree, symlinks=True)
    outcome = librarian.dream_copy(
        copy,
        corpus=[reproduction.payloads],
        as_of=eval_set.as_of,
        owner=eval_set.owner.to_owner(),
        time_zone=time_zone,
        config=config,
    )
    print(f"reproduction dream: {librarian.dreaming_report(outcome)}")
    return 0
