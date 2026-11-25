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

A dreamed eval wiki is a copy of one that dreaming has run over
(`pa librarian dream TARGET --corpus DIR`), so that dreaming's effect can be measured against
the same wiki without it. Its run log holds the replay's runs, then the copy's dreaming runs,
which name the librarian version that dreamed it; the results record both. Any other mix is
refused (`pa_core.librarian.replay.run_history`). Its tree may also hold what dreaming leaves
at a wiki's root: `proposals.md`, what it asked the owner, and `retired.md`, the slugs of the
pages it retired. The assistant is given both with the rest of the tree.

A run over a cut (`pa_home.cut`) is given the cut's own wiki instead: the live wiki at its last
commit when the cut was taken (`cut_wiki`). Its librarian version is the one that commit names.
Or it is given a dreamed copy of the cut's wiki (`dreamed_cut_wiki`,
`pa librarian dream TARGET --cut DIR`), whose run log holds the copy's dreaming runs alone. The
copy is the owner's data, as the cut is, and is kept under the data root.

A run over a released reproduction (`pa_evals.reproduction`) is given the reproduction's
starting wiki (`reproduction_wiki`). The diagnostician wrote it, so it has no run log, and the
results name no librarian version for it (`NO_VERSION`). Or it is given a dreamed copy of that
wiki (`dreamed_reproduction_wiki`, `pa-eval reproduction dream DIR COPY`), whose run log holds
the copy's dreaming runs alone, as of the reproduction's eval set's as-of time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pa_core.errors import PaError
from pa_core.librarian.replay import ReplayTarget, RunHistory, RunLogError, run_history
from pa_evals.reproduction import Released
from pa_home.cut import Cut


class EvalWikiError(PaError):
    """A directory that isn't a finished eval wiki."""


@dataclass(frozen=True)
class EvalWiki:
    tree: Path
    """The wiki's tree, laid beside the throwaway L0 for the run."""
    librarian_version: str
    """The librarian version that built it, from its run log."""
    dreaming_version: str | None = None
    """The librarian version that dreamed it, from its run log; None for a wiki no dreaming
    run has changed."""


def load_eval_wiki(directory: Path) -> EvalWiki:
    """The eval wiki in `directory`: its tree, and the librarian versions its run log names.

    Refused unless the run log holds a replay's runs, with a committed one, all of one
    librarian version, and the last one committed (a replay that stopped on a failed run
    isn't finished); then, for a dreamed eval wiki, dreaming runs of one version.
    """
    if not directory.is_dir():
        raise EvalWikiError(f"eval wiki not found: {directory}")
    layout = ReplayTarget(directory)
    tree, run_log = layout.tree, layout.run_log
    if not tree.is_dir():
        raise EvalWikiError(f"{directory} has no {tree.name}/: it isn't an eval wiki")
    if not run_log.is_file():
        raise EvalWikiError(f"{directory} has no {run_log.name}: it isn't an eval wiki")
    history = _history(run_log)
    if not history.replays:
        raise EvalWikiError(
            f"{run_log} has no replay run" if history.dreams else f"{run_log} has no committed run"
        )
    return EvalWiki(tree, history.replays[-1].librarian_version, history.dreaming_version)


UNNAMED_VERSION = "unnamed (the cut's wiki commit names none)"
"""The librarian version of a cut's wiki whose last commit names none, such as a seed."""


def cut_wiki(cut: Cut) -> EvalWiki:
    """The wiki a cut holds, as a files-plus-wiki run over the cut is given it."""
    if cut.wiki is None or cut.manifest.wiki is None:
        raise EvalWikiError(f"the cut {cut.name} holds no wiki: the data root had none")
    return EvalWiki(cut.wiki, cut.manifest.wiki.librarian_version or UNNAMED_VERSION)


def dreamed_cut_wiki(cut: Cut, directory: Path) -> EvalWiki:
    """The dreamed copy of `cut`'s wiki in `directory`, which a files-plus-wiki run over the
    cut is given in place of the cut's own: its tree, the librarian version the cut names, and
    the one that dreamed the copy, from its run log.

    Refused unless it is laid out as `pa librarian dream DIR --cut CUT` leaves it, a `wiki/`
    and a run log, and the run log holds only dreaming runs, a committed one among them, all
    by one version, of this cut's wiki: as of the cut's as-of time, and of a wiki built by the
    librarian version the cut names.
    """
    if cut.wiki is None or cut.manifest.wiki is None:
        raise EvalWikiError(f"the cut {cut.name} holds no wiki: the data root had none")
    not_a_copy = (
        f"it isn't a dreamed copy of the cut's wiki (pa librarian dream DIR --cut {cut.path})"
    )
    tree, run_log, history = _dreamed_alone(directory, not_a_copy)
    built_by = cut.manifest.wiki.librarian_version
    other = history.dreamed_otherwise(as_of=cut.as_of, librarian_version=built_by)
    if other is not None and other.as_of != cut.as_of:
        raise EvalWikiError(
            f"{run_log} has a dreaming run as of {other.as_of.isoformat()}, not the cut's "
            f"as-of time ({cut.as_of.isoformat()}): {not_a_copy}"
        )
    if other is not None:
        raise EvalWikiError(
            f"{run_log} has a dreaming run of a wiki built by librarian version "
            f"{other.librarian_version}, not {built_by}, which the cut names: {not_a_copy}"
        )
    return EvalWiki(tree, built_by or UNNAMED_VERSION, history.dreaming_version)


NO_VERSION = "none (the diagnostician wrote a reproduction's starting wiki)"
"""What stands for the librarian version of a reproduction's starting wiki: no librarian built
it, so results asked over it name none."""


def reproduction_wiki(reproduction: Released) -> EvalWiki:
    """The starting wiki a released reproduction holds, as a files-plus-wiki run over the
    reproduction is given it. It has no run log, and needs none."""
    if reproduction.wiki is None:
        raise EvalWikiError(_NO_STARTING_WIKI.format(finding=reproduction.finding))
    return EvalWiki(reproduction.wiki, NO_VERSION)


def dreamed_reproduction_wiki(
    reproduction: Released, directory: Path, *, as_of: datetime
) -> EvalWiki:
    """The dreamed copy of `reproduction`'s starting wiki in `directory`, which a
    files-plus-wiki run over the reproduction is given in place of the starting wiki: its
    tree, and the librarian version that dreamed the copy, from its run log. No version built
    the wiki, so none is named.

    Refused unless it is laid out as `pa-eval reproduction dream DIR COPY` leaves it, a `wiki/`
    and a run log, and the run log holds only dreaming runs, a committed one among them, all
    by one version, of this reproduction's wiki as far as a run log can say: as of `as_of`,
    its eval set's as-of time, and of a wiki no librarian version built.
    """
    if reproduction.wiki is None:
        raise EvalWikiError(_NO_STARTING_WIKI.format(finding=reproduction.finding))
    not_a_copy = (
        "it isn't a dreamed copy of the reproduction's starting wiki "
        "(pa-eval reproduction dream DIR COPY)"
    )
    tree, run_log, history = _dreamed_alone(directory, not_a_copy)
    other = history.dreamed_otherwise(as_of=as_of, librarian_version=None)
    if other is not None and other.as_of != as_of:
        raise EvalWikiError(
            f"{run_log} has a dreaming run as of {other.as_of.isoformat()}, not the eval "
            f"set's as-of time ({as_of.astimezone(UTC).isoformat()}): {not_a_copy}"
        )
    if other is not None:
        raise EvalWikiError(
            f"{run_log} has a dreaming run of a wiki built by librarian version "
            f"{other.librarian_version}, and no librarian version built a reproduction's "
            f"starting wiki: {not_a_copy}"
        )
    return EvalWiki(tree, NO_VERSION, history.dreaming_version)


_NO_STARTING_WIKI = (
    "the reproduction {finding} holds no starting wiki: its fault needs none, so ask it with "
    "files alone"
)


def _dreamed_alone(directory: Path, not_a_copy: str) -> tuple[Path, Path, RunHistory]:
    """The tree, the run log, and the run history of the copy in `directory`, which was
    dreamed and never replayed. Refused, as `not_a_copy`, unless it is laid out as a dreaming
    run leaves a copy, a `wiki/` and a run log, and the run log holds only dreaming runs, a
    committed one among them."""
    layout = ReplayTarget(directory)
    tree, run_log = layout.tree, layout.run_log
    if not tree.is_dir():
        raise EvalWikiError(f"{directory} has no {tree.name}/: {not_a_copy}")
    if not run_log.is_file():
        raise EvalWikiError(f"{directory} has no {run_log.name}: {not_a_copy}")
    history = _history(run_log)
    if history.replays:
        raise EvalWikiError(f"{run_log} has a replay's runs: {not_a_copy}")
    if history.dreaming_version is None:
        raise EvalWikiError(f"{run_log} has no committed dreaming run: {not_a_copy}")
    return tree, run_log, history


def _history(run_log: Path) -> RunHistory:
    try:
        return run_history(run_log)
    except RunLogError as error:
        raise EvalWikiError(str(error)) from error
