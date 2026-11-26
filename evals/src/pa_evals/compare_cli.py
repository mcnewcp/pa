"""`pa-eval compare`: put run directories side by side in one comparison report.

`pa-eval compare-wikis`: write the real-data wiki comparison (see `pa_evals.wiki_comparison`).
Its runs and its private analysis are real data: each is refused inside the repository before
anything is read or written. Its public report holds numbers only, so it can be committed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.atomic import write_atomically
from pa_evals.comparison import ComparisonError, failed_to_run, load_runs, render_comparison
from pa_evals.real_data import require_private
from pa_evals.wiki_comparison import check_wiki_runs, render_private, render_public, wiki_runs


def add_compare_command(commands: Any) -> None:
    compare = commands.add_parser(
        "compare",
        help="Compare run directories (from pa-eval run, or committed baselines) side by side.",
        description="Print a comparison report of the run directories, each labelled by its "
        "path, the first being the reference. A run directory holds one run's results.json, "
        "or a run-<n>/ per run from pa-eval run --runs N.",
    )
    compare.add_argument("run_dirs", nargs="+", type=Path, metavar="RUN_DIR")
    compare.set_defaults(handler=_compare)


def add_compare_wikis_command(commands: Any) -> None:
    compare = commands.add_parser(
        "compare-wikis",
        help="Write the real-data wiki comparison: public numbers, and a private failure analysis.",
        description="Compare files plus wiki over the same cut with two eval wikis, the second "
        "against the first (such as a dreamed copy of the cut's wiki against the cut's own), "
        "and write the public report (numbers only) and the private one (the failure analysis, "
        "refused inside the repository). The second wiki passes when no question fell (right "
        "in every run on the first wiki, wrong in every run on the second) and its drop from "
        "the first is no larger than the noise floor, overall and in every tier of the eval "
        "set (every category, for a set without tiers): the largest drop between any two "
        "halves of the first wiki's own runs. A drop of one of the second wiki's answers is "
        "allowed whatever the floor. So each wiki needs at least 3 runs, and the first at "
        "least twice as many as the second. A comparison in which any answer failed to run, "
        "on either wiki, isn't judged: the reports are written and say how many failed, and "
        "those runs need running again. Every run of both wikis must be at one commit of "
        "this workspace. A wiki's runs may be in several run directories.",
    )
    compare.add_argument("--first", type=Path, required=True, nargs="+", metavar="RUN_DIR")
    compare.add_argument("--second", type=Path, required=True, nargs="+", metavar="RUN_DIR")
    compare.add_argument("--public", type=Path, required=True, metavar="PATH")
    compare.add_argument("--private", type=Path, required=True, metavar="PATH")
    compare.set_defaults(handler=_compare_wikis)


def _compare_wikis(args: argparse.Namespace) -> int:
    first_dirs = [require_private(run_dir, "--first") for run_dir in args.first]
    second_dirs = [require_private(run_dir, "--second") for run_dir in args.second]
    private = require_private(args.private, "--private")
    if len({*first_dirs, *second_dirs}) != len(first_dirs) + len(second_dirs):
        raise ComparisonError("each run directory can be given only once")
    first = [run for run_dir in first_dirs for run in load_runs(run_dir)]
    second = [run for run_dir in second_dirs for run in load_runs(run_dir)]
    check_wiki_runs("--first", first)
    check_wiki_runs("--second", second)
    runs = wiki_runs(first, second)
    public = render_public(runs)
    write_atomically(private, render_private(runs).encode(), mode=0o600)
    write_atomically(args.public, public.encode())
    if runs.comparison.passed is None:
        failed = failed_to_run(*runs.labels, runs.comparison)
        outcome = f"isn't judged: {failed}, so those runs need running again"
    else:
        outcome = "passes" if runs.comparison.passed else "fails"
    print(f"compare-wikis: {runs.labels[1]} {outcome}; public {args.public}, private {private}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    labels = [str(run_dir) for run_dir in args.run_dirs]
    if len(set(labels)) != len(labels):
        raise ComparisonError("each run directory can be compared only once")
    print(render_comparison({str(d): load_runs(d) for d in args.run_dirs}), end="")
    return 0
