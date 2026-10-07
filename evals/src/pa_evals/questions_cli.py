"""`pa-eval eval-set build`: build the eval set JSON from the questions and the corpus."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_evals import frozen
from pa_evals.questions import build_eval_set
from pa_evals.storyline import load_spec


def add_eval_set_command(commands: Any) -> None:
    eval_set = commands.add_parser("eval-set", help="Manage the eval set.")
    eval_set_commands = eval_set.add_subparsers(dest="eval_set_command", required=True)
    build = eval_set_commands.add_parser(
        "build",
        help="Build the eval set from the questions, the storyline spec and the corpus.",
        description="Build the eval set JSON from questions whose evidence names spec events. "
        "Each event's episode id is worked out from the generated corpus with ingest's rules. "
        "Rebuild after editing the questions or regenerating the corpus.",
    )
    build.add_argument(
        "--questions",
        type=Path,
        default=frozen.QUESTIONS,
        help="Questions YAML (default: %(default)s).",
    )
    build.add_argument(
        "--spec", type=Path, default=frozen.SPEC, help="Storyline spec (default: %(default)s)."
    )
    build.add_argument(
        "--corpus",
        type=Path,
        default=frozen.PAYLOADS,
        help="The corpus generated from the spec (default: %(default)s).",
    )
    build.add_argument(
        "--out", type=Path, default=frozen.EVAL_SET, help="Eval set JSON (default: %(default)s)."
    )
    build.set_defaults(handler=_build)


def _build(args: argparse.Namespace) -> int:
    eval_set = build_eval_set(args.questions, load_spec(args.spec), args.corpus)
    args.out.write_text(eval_set.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(eval_set.questions)} questions to {args.out}")
    return 0
