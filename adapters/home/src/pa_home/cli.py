"""The `pa` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pa_core.errors import PaError
from pa_core.ingest import ingest
from pa_home.config import DataRoot, load_data_root
from pa_home.filesystem_l0 import FilesystemL0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa", description="Personal assistant.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="Turn raw payloads in the inbox into episodes in L0.")
    args = parser.parse_args(argv)

    try:
        data_root = load_data_root()
        if args.command == "ingest":
            _ingest(data_root)
    except PaError as error:
        print(f"pa: {error}", file=sys.stderr)
        return 1
    return 0


def _ingest(data_root: DataRoot) -> None:
    data_root.inbox.mkdir(parents=True, exist_ok=True)
    summary = ingest(data_root.inbox, FilesystemL0(data_root.l0))
    print(f"ingested {summary.ingested}, unchanged {summary.unchanged}")
