"""`pa-eval corpus generate`: write a synthetic corpus from a storyline spec.

A rare, deliberate command: its output is committed as the frozen corpus.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.model_client import ModelClient
from pa_evals.corpus import generate_corpus
from pa_evals.storyline import load_spec
from pa_home.claude_model import ClaudeCliBackend


def add_corpus_command(commands: Any) -> None:
    corpus = commands.add_parser("corpus", help="Manage the synthetic corpus.")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    generate = corpus_commands.add_parser(
        "generate",
        help="Generate a synthetic corpus of raw payloads from a storyline spec.",
        description="Generate a synthetic corpus of raw payloads from a storyline spec. "
        "Metadata comes from the spec; claude -p writes only prose, one call per email, daily "
        "note section, and correction.",
    )
    generate.add_argument("spec", type=Path, help="The storyline spec (YAML).")
    generate.add_argument("out", type=Path, help="An empty or new directory for the payloads.")
    generate.add_argument(
        "--model", help="The claude model that writes the prose (default: the CLI's default)."
    )
    generate.set_defaults(handler=_generate)


def _generate(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    client = ModelClient(ClaudeCliBackend(model=args.model))
    payloads = generate_corpus(spec, client, args.out)
    print(f"generated {len(payloads)} payloads in {args.out}")
    return 0
