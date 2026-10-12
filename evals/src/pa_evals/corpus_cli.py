"""`pa-eval corpus generate`: write a synthetic corpus from a storyline spec.

A rare, deliberate command: its output is committed as the frozen corpus.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from pa_core.model_client import ModelClient
from pa_evals.corpus import DEFAULT_WORKERS, generate_corpus
from pa_evals.storyline import load_spec
from pa_home.claude_model import ClaudeCliBackend


def add_corpus_command(commands: Any) -> None:
    corpus = commands.add_parser("corpus", help="Manage the synthetic corpus.")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    generate = corpus_commands.add_parser(
        "generate",
        help="Generate a synthetic corpus of raw payloads from a storyline spec.",
        description="Generate a synthetic corpus of raw payloads from a storyline spec. "
        "People, times, and calendars come from the spec; claude -p writes only prose, one call "
        "per email, daily note section, correction, and assistant chat, and one per 25 bulk "
        "emails. Given extension files after the spec, only the extension's payloads are "
        "written. Every reply is cached as it arrives, so a failed run resumes where it stopped.",
    )
    generate.add_argument(
        "spec",
        type=Path,
        nargs="+",
        help="The storyline spec (YAML), then any extension files that add to it.",
    )
    generate.add_argument("out", type=Path, help="An empty or new directory for the payloads.")
    generate.add_argument(
        "--model", help="The claude model that writes the prose (default: the CLI's default)."
    )
    generate.add_argument(
        "--cache-dir",
        type=Path,
        help="Where model replies are cached, by prompt "
        "(default: $XDG_CACHE_HOME/pa/corpus-prose/<model>, or under ~/.cache).",
    )
    generate.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"How many model calls run at once (default: {DEFAULT_WORKERS}).",
    )
    generate.set_defaults(handler=_generate)


def _generate(args: argparse.Namespace) -> int:
    base, *extensions = args.spec
    spec = load_spec(base, *extensions)
    client = ModelClient(ClaudeCliBackend(model=args.model))
    cache_dir = args.cache_dir or _default_cache_dir(args.model)
    payloads = generate_corpus(spec, client, args.out, cache_dir=cache_dir, workers=args.workers)
    print(f"generated {len(payloads)} payloads in {args.out}")
    return 0


def _default_cache_dir(model: str | None) -> Path:
    """The user's cache, one directory per model, since each writes its own prose."""
    cache_home = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache_home) / "pa" / "corpus-prose" / (model or "default")
