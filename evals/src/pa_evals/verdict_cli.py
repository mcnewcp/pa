"""`pa-eval verdict`: write the real-data verdict (see `pa_evals.verdict`).

The real runs and the private verdict are real data: each is refused inside the repository
before anything is read or written. The public verdict holds numbers only, so it can be
committed. The scale corpus's runs default to v0.12.1's committed baselines.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.atomic import write_atomically
from pa_evals.comparison import load_runs
from pa_evals.real_data import require_private
from pa_evals.verdict import (
    FILES_ALONE,
    FILES_PLUS_WIKI,
    check_runs,
    check_same_cut,
    render_private,
    render_public,
)

BASELINES = Path(__file__).resolve().parents[2] / "baselines" / "v0.12.1"


def add_verdict_command(commands: Any) -> None:
    verdict = commands.add_parser(
        "verdict",
        help="Write the real-data verdict: public numbers, and a private failure analysis.",
        description="Compare files plus wiki with files alone over the same cut, and write the "
        "public verdict (numbers only) and the private one (the failure analysis, refused "
        "inside the repository).",
    )
    verdict.add_argument("--files-alone", type=Path, required=True, metavar="RUN_DIR")
    verdict.add_argument("--files-plus-wiki", type=Path, required=True, metavar="RUN_DIR")
    verdict.add_argument(
        "--scale-files-alone",
        type=Path,
        default=BASELINES / "scale-files-alone-rerun",
        metavar="RUN_DIR",
    )
    verdict.add_argument(
        "--scale-files-plus-wiki",
        type=Path,
        default=BASELINES / "scale-files-plus-wiki",
        metavar="RUN_DIR",
    )
    verdict.add_argument("--public", type=Path, required=True, metavar="PATH")
    verdict.add_argument("--private", type=Path, required=True, metavar="PATH")
    verdict.set_defaults(handler=_verdict)


def _verdict(args: argparse.Namespace) -> int:
    files_alone_dir = require_private(args.files_alone, "--files-alone")
    files_plus_wiki_dir = require_private(args.files_plus_wiki, "--files-plus-wiki")
    private = require_private(args.private, "--private")
    files_alone = load_runs(files_alone_dir)
    files_plus_wiki = load_runs(files_plus_wiki_dir)
    check_runs("--files-alone", FILES_ALONE, files_alone)
    check_runs("--files-plus-wiki", FILES_PLUS_WIKI, files_plus_wiki)
    check_same_cut([*files_alone, *files_plus_wiki])
    public = render_public(
        files_alone,
        files_plus_wiki,
        load_runs(args.scale_files_alone),
        load_runs(args.scale_files_plus_wiki),
    )
    private.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(private, render_private(files_alone, files_plus_wiki).encode(), mode=0o600)
    args.public.write_text(public)
    print(f"verdict: public {args.public}, private {private}")
    return 0
