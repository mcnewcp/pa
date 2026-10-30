"""Eval wikis: the wiki a files-plus-wiki run gives the assistant, built by a replay on the host.

An eval wiki is a directory holding a replay's final tree and its run log, as
`pa librarian replay TARGET` leaves them in TARGET (`pa_core.librarian.replay.ReplayTarget`):

    wiki/            the wiki's tree: index.md, days/, people/, orgs/, topics/
    run-log.jsonl    one entry per librarian run (`pa_core.librarian.replay.RunLogEntry`)

The replay's git directory (`wiki.git/` beside them) is not part of it, and is never committed.
Eval wikis are built once per librarian version and kept in the evals tree by corpus and
librarian version (`frozen.eval_wiki`): `evals/wikis/<corpus>/<librarian version>/`. One is
committed when a reported result uses it, so a failure can be diagnosed from the repository.

The run log names the librarian version that built the wiki; the results record it.

A run over a cut (`pa_home.cut`) is given the cut's own wiki instead: the live wiki at its last
commit when the cut was taken (`cut_wiki`). Its librarian version is the one that commit names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pa_core.errors import PaError
from pa_core.librarian.replay import ReplayTarget, RunLogEntry, RunLogError, read_run_log
from pa_home.cut import Cut


class EvalWikiError(PaError):
    """A directory that isn't a finished eval wiki."""


@dataclass(frozen=True)
class EvalWiki:
    tree: Path
    """The wiki's tree, laid beside the throwaway L0 for the run."""
    librarian_version: str
    """The librarian version that built it, from its run log."""


def load_eval_wiki(directory: Path) -> EvalWiki:
    """The eval wiki in `directory`: its tree, and the librarian version its run log names.

    Refused unless the run log has a committed run, every run in it is of one librarian version,
    and its last run committed (a replay that stopped on a failed run isn't finished).
    """
    if not directory.is_dir():
        raise EvalWikiError(f"eval wiki not found: {directory}")
    layout = ReplayTarget(directory)
    tree, run_log = layout.tree, layout.run_log
    if not tree.is_dir():
        raise EvalWikiError(f"{directory} has no {tree.name}/: it isn't an eval wiki")
    if not run_log.is_file():
        raise EvalWikiError(f"{directory} has no {run_log.name}: it isn't an eval wiki")
    entries = _entries(run_log)
    if not any(entry.status == "committed" for entry in entries):
        raise EvalWikiError(f"{run_log} has no committed run")
    versions = sorted({entry.librarian_version for entry in entries})
    if len(versions) > 1:
        raise EvalWikiError(f"{run_log} names more than one librarian version: {versions}")
    if entries[-1].status != "committed":
        raise EvalWikiError(
            f"{run_log} ends with a failed run: finish the replay with --resume first"
        )
    return EvalWiki(tree, versions[0])


UNNAMED_VERSION = "unnamed (the cut's wiki commit names none)"
"""The librarian version of a cut's wiki whose last commit names none, such as a seed."""


def cut_wiki(cut: Cut) -> EvalWiki:
    """The wiki a cut holds, as a files-plus-wiki run over the cut is given it."""
    if cut.wiki is None or cut.manifest.wiki is None:
        raise EvalWikiError(f"the cut {cut.name} holds no wiki: the data root had none")
    return EvalWiki(cut.wiki, cut.manifest.wiki.librarian_version or UNNAMED_VERSION)


def _entries(run_log: Path) -> list[RunLogEntry]:
    try:
        return read_run_log(run_log)
    except RunLogError as error:
        raise EvalWikiError(str(error)) from error
