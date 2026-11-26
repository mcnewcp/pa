"""How a command is told a case (see `pa_evals.case`): `pa-eval diagnose`, where the session
runs, and `pa-eval release`, on the devbox, are told the same parts, each by where that
machine holds its copy.

Every part is real data, so every path is refused inside the repository, after following
links, before any is read.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_evals.case import Case, CaseError, load_case
from pa_evals.diagnosis import earlier_analysis
from pa_evals.real_data import require_private


def add_case_arguments(command: Any) -> None:
    """Adds the options that name a case to `command`."""
    command.add_argument(
        "--cut", type=Path, required=True, metavar="DIR", help="The case: a cut (`pa cut`)."
    )
    command.add_argument(
        "--eval-set",
        type=Path,
        metavar="FILE",
        help="In the case: the eval set asked over the cut, with its expected answers.",
    )
    command.add_argument(
        "--runs",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="In the case: a run directory of eval runs over the cut, under the label the "
        "referral gives its set of runs. Repeat it for each run directory; one label may have "
        "several.",
    )
    command.add_argument(
        "--wiki",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="In the case: a wiki's tree (the cut's own, or a dreamed copy's `wiki/`), under "
        "the label the referral gives it. Repeat it for each wiki.",
    )
    command.add_argument(
        "--history",
        action="append",
        default=[],
        metavar="LABEL=DIR",
        help="In the case: the history of the wiki under LABEL, as `pa-eval wiki-history` "
        "exported it.",
    )
    command.add_argument(
        "--earlier",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help="In the case: an earlier diagnosis's directory, whose private analysis this one "
        "builds on. Repeat it for each.",
    )


@dataclass(frozen=True)
class NamedCase:
    """A case as a command was told it: every path resolved, and none inside the repository.
    Nothing has been read yet."""

    cut: Path
    eval_set: Path | None
    runs: dict[str, tuple[Path, ...]]
    wikis: dict[str, Path]
    histories: dict[str, Path]
    earlier: tuple[Path, ...]

    def load(self) -> Case:
        """The case, refused (CaseError) unless each part is what it was named as."""
        analyses = dict(earlier_analysis(directory) for directory in self.earlier)
        if len(analyses) != len(self.earlier):
            raise CaseError("--earlier names two diagnoses of one name")
        return load_case(
            self.cut,
            eval_set=self.eval_set,
            runs=self.runs,
            wikis=self.wikis,
            histories=self.histories,
            earlier=analyses,
        )


def named_case(args: argparse.Namespace) -> NamedCase:
    """The case `args` names, with every path checked: none may be inside the repository."""
    runs: dict[str, tuple[Path, ...]] = {}
    for label, path in _labelled(args.runs, "--runs"):
        runs[label] = (*runs.get(label, ()), path)
    return NamedCase(
        cut=require_private(args.cut, "--cut"),
        eval_set=None if args.eval_set is None else require_private(args.eval_set, "--eval-set"),
        runs=runs,
        wikis=_one_each(args.wiki, "--wiki"),
        histories=_one_each(args.history, "--history"),
        earlier=tuple(require_private(path, "--earlier") for path in args.earlier),
    )


def _labelled(values: Sequence[str], option: str) -> list[tuple[str, Path]]:
    """Each `LABEL=DIR` of `values`, as the label and the directory, checked."""
    named = []
    for value in values:
        label, equals, path = value.partition("=")
        if not (label and equals and path):
            raise CaseError(f"{option} takes LABEL=DIR (got {value!r})")
        named.append((label, require_private(Path(path), f"{option} {label}")))
    return named


def _one_each(values: Sequence[str], option: str) -> dict[str, Path]:
    named = _labelled(values, option)
    by_label = dict(named)
    if len(by_label) != len(named):
        raise CaseError(f"{option} names one label twice: a label is one wiki's")
    return by_label
