"""A case: the real material a diagnosis reads, all of it frozen (ADR-0010).

A case is a cut, and beside it whatever else the referral is about. Every part but the cut is
optional:

- the eval set the PA was asked, with its expected answers;
- sets of eval runs over the cut, each set under a label the referral gives it;
- wikis, each under a label: the cut's own, or a dreamed copy of it. A wiki comes with its
  history as files (`pa_evals.wiki_history`), since a session has no git directory to read;
- earlier diagnoses' private analyses, which a follow-up referral builds on.

The live store is never a part of a case: a diagnosis reads something that holds still. So the
cut must be a cut, as `pa cut` takes one, and a wiki a cut's own or a copy of it, never the
data root's.

This module owns what a case holds and how it is laid for the session, so nothing else does.
The layout is fixed, and the diagnostician's instructions name it, so a referral can say "the
runs under `dreamed`":

    <case>/cut/                   the cut, laid out as a data root is
        cut.json                  its manifest
        l0/                       every episode's envelope and raw payload
        wiki/                     the wiki's tree, if the cut holds one
        status/status.json        the status file, if the cut holds one
    <case>/eval-set.json          the eval set
    <case>/runs/<label>/          a set of runs
        run-<n>/results.json      each run's results, numbered in the order the set was named
    <case>/wikis/<label>/         a wiki
        wiki/                     its tree
        history/                  its history: a file per run, oldest first
    <case>/earlier/<name>/        an earlier diagnosis, by its name
        analysis.md               its private analysis

The code as deployed isn't laid: the session is pointed at the checkout it runs from.

Everything in a case is real content. Nothing here puts a line of it in an exception: an
error names a path, a label, a cut, or an episode's id.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

from pa_core.errors import PaError
from pa_evals.comparison import ComparisonError, load_runs, run_files
from pa_evals.eval_set import EvalSet, load_eval_set
from pa_evals.findings import LABEL
from pa_evals.harness import lay_cut, make_read_only
from pa_evals.results import RunResults
from pa_home.config import DataRoot, is_live_store
from pa_home.cut import MANIFEST, Cut, load_cut

_LABEL = re.compile(LABEL)

EVAL_SET = "eval-set.json"
RUNS = "runs"
WIKIS = "wikis"
WIKI_TREE = "wiki"
WIKI_HISTORY = "history"
EARLIER = "earlier"
EARLIER_ANALYSIS = "analysis.md"


class CaseError(PaError):
    """Parts that don't make a case. Its message never quotes what a part holds."""


@dataclass(frozen=True)
class CaseWiki:
    """A wiki in a case."""

    tree: Path
    history: Path | None = None
    """Its history, as `pa-eval wiki-history` exported it: a directory of files, one per run."""

    def history_files(self) -> list[Path]:
        """The files of its history, oldest first (none without one)."""
        return [] if self.history is None else _regular_files(self.history)


@dataclass(frozen=True)
class Case:
    """The real material a diagnosis reads, all of it frozen."""

    cut: Cut
    eval_set: Path | None = None
    """The eval set's file."""
    runs: Mapping[str, Sequence[Path]] = field(default_factory=dict)
    """Each set of runs, by its label: the run directories that hold it, in order."""
    wikis: Mapping[str, CaseWiki] = field(default_factory=dict)
    """Each wiki, by its label."""
    earlier: Mapping[str, Path] = field(default_factory=dict)
    """Each earlier diagnosis's private analysis (the file), by the diagnosis's name."""

    def read_eval_set(self) -> EvalSet | None:
        """The eval set, if the case holds one."""
        return None if self.eval_set is None else load_eval_set(self.eval_set, cut=self.cut)

    def results(self, label: str) -> list[RunResults]:
        """The runs under `label`, in order."""
        return [run for directory in self.runs[label] for run in _runs(directory)]

    def runs_under(self, label: str) -> int:
        """How many runs the set under `label` holds."""
        return sum(len(_run_files(directory)) for directory in self.runs[label])

    def question_ids(self) -> frozenset[str]:
        """The id of every question in the case: the eval set's, and those its runs asked."""
        eval_set = self.read_eval_set()
        asked = {question.id for question in eval_set.questions} if eval_set else set()
        for label in self.runs:
            asked |= {question.id for run in self.results(label) for question in run.questions}
        return frozenset(asked)


def load_case(
    cut_dir: Path,
    *,
    eval_set: Path | None = None,
    runs: Mapping[str, Sequence[Path]] | None = None,
    wikis: Mapping[str, Path] | None = None,
    histories: Mapping[str, Path] | None = None,
    earlier: Mapping[str, Path] | None = None,
) -> Case:
    """The case made of the cut in `cut_dir` and the parts named, each refused (CaseError)
    unless it is what it is named as:

    - `eval_set`: an eval set asked at the cut's as-of time, of episodes the cut holds;
    - `runs`: by label, run directories holding eval runs asked over this cut;
    - `wikis`: by label, a wiki's tree;
    - `histories`: by a wiki's label, the directory its history was exported to;
    - `earlier`: by an earlier diagnosis's name, the file of its private analysis.
    """
    cut = _a_cut(cut_dir)
    runs, wikis, histories = runs or {}, wikis or {}, histories or {}
    for label in (*runs, *wikis, *histories):
        if not _LABEL.fullmatch(label):
            raise CaseError(
                "a label is lowercase letters, digits, and hyphens, at most 32, starting with a "
                f"letter or a digit (got {label!r})"
            )
    for label, tree in wikis.items():
        if not _is_directory(tree):
            raise CaseError(f"the wiki under {label} ({tree}) isn't a directory")
        if is_live_store(tree.parent):
            raise CaseError(
                f"the wiki under {label} ({tree}) is a live store's: a case holds a cut's own "
                "wiki, or a dreamed copy of it, never the wiki the librarian is writing"
            )
    for label, history in histories.items():
        if label not in wikis:
            raise CaseError(f"there is a history under {label}, and no wiki: name its wiki too")
        if not _is_directory(history):
            raise CaseError(f"the history under {label} ({history}) isn't a directory")
    for name, analysis in (earlier or {}).items():
        if analysis.is_symlink() or not analysis.is_file():
            raise CaseError(f"the earlier diagnosis {name} has no private analysis ({analysis})")
    case = Case(
        cut=cut,
        eval_set=eval_set,
        runs={label: tuple(directories) for label, directories in runs.items()},
        wikis={label: CaseWiki(tree, histories.get(label)) for label, tree in wikis.items()},
        earlier=dict(earlier or {}),
    )
    case.read_eval_set()
    for label, directories in case.runs.items():
        for directory in directories:
            if any(run.cut is None or run.cut.name != cut.name for run in _runs(directory)):
                raise CaseError(
                    f"a run under {label} ({directory}) wasn't asked over the cut {cut.name}"
                )
    return case


def _a_cut(directory: Path) -> Cut:
    """The cut in `directory`, refused (CaseError) unless it is one as `pa cut` takes it: named
    for the time it was taken, and holding nothing that only a live store does. A manifest
    doesn't make a data root a cut."""
    cut = load_cut(directory)
    if is_live_store(directory) or DataRoot(directory).wiki_git.exists(follow_symlinks=False):
        raise CaseError(
            f"{directory} isn't a cut: it holds what only a live store does. A case holds a cut "
            "of the store (`pa cut` takes one), never the store itself"
        )
    if cut.name != f"{cut.as_of.astimezone(UTC):%Y%m%dT%H%M%SZ}":
        raise CaseError(
            f"{directory} isn't a cut: it isn't named for the time it was taken, as `pa cut` "
            "names one"
        )
    return cut


def lay_case(case: Case, target: Path) -> None:
    """Copy `case` to `target`, laid out as this module's docstring says, with nothing in it
    writable. Only regular files are copied: nothing is read through a link."""
    cut = target / "cut"
    cut.mkdir(parents=True)
    lay_cut(case.cut, DataRoot(cut))
    if case.cut.wiki is not None:
        shutil.copytree(case.cut.wiki, DataRoot(cut).wiki)
    shutil.copyfile(case.cut.path / MANIFEST, cut / MANIFEST)
    if case.eval_set is not None:
        shutil.copyfile(case.eval_set, target / EVAL_SET)
    for label, directories in case.runs.items():
        results = [file for directory in directories for file in _run_files(directory)]
        for number, file in enumerate(results, start=1):
            _copy(file, target / RUNS / label / f"run-{number}" / file.name)
    for label, wiki in case.wikis.items():
        for file in _regular_files(wiki.tree):
            _copy(file, target / WIKIS / label / WIKI_TREE / file.relative_to(wiki.tree))
        for file in wiki.history_files():
            _copy(file, target / WIKIS / label / WIKI_HISTORY / file.name)
    for name, analysis in case.earlier.items():
        _copy(analysis, target / EARLIER / name / EARLIER_ANALYSIS)
    make_read_only(target)


def _runs(directory: Path) -> list[RunResults]:
    """The runs in a run directory. What is wrong with one that can't be read is never said:
    it would quote the run."""
    try:
        return load_runs(directory)
    except (ComparisonError, OSError, ValueError):
        raise CaseError(f"{directory} doesn't hold eval runs") from None


def _run_files(directory: Path) -> list[Path]:
    try:
        return run_files(directory)
    except ComparisonError:
        raise CaseError(f"{directory} doesn't hold eval runs") from None


def _is_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _regular_files(top: Path) -> list[Path]:
    """Every regular file under `top`, in order, without following a link."""
    return sorted(
        directory / name
        for directory, _, names in top.walk()
        for name in names
        if not (directory / name).is_symlink() and (directory / name).is_file()
    )


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
