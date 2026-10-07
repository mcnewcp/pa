"""The `pa-eval` command.

Each subcommand lives in its own module, which adds its parser and sets a `handler` that takes
the parsed arguments and returns the exit code.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pa_core.errors import PaError
from pa_evals.corpus_cli import add_corpus_command
from pa_evals.questions_cli import add_eval_set_command
from pa_evals.run_cli import add_run_command


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa-eval", description="Personal assistant evals.")
    commands = parser.add_subparsers(dest="command", required=True)
    add_corpus_command(commands)
    add_eval_set_command(commands)
    add_run_command(commands)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except PaError as error:
        print(f"pa-eval: {error}", file=sys.stderr)
        return 1
