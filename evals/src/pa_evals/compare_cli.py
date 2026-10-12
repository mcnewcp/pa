"""`pa-eval compare`: put run directories side by side in one comparison report."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_evals.comparison import ComparisonError, load_runs, render_comparison


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


def _compare(args: argparse.Namespace) -> int:
    labels = [str(run_dir) for run_dir in args.run_dirs]
    if len(set(labels)) != len(labels):
        raise ComparisonError("each run directory can be compared only once")
    print(render_comparison({str(d): load_runs(d) for d in args.run_dirs}), end="")
    return 0
