"""The `pa` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pa_core.catalog import open_catalog, rebuild_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest
from pa_home.config import DataRoot, load_data_root, load_owner
from pa_home.filesystem_l0 import FilesystemL0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa", description="Personal assistant.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="Turn raw payloads in the inbox into episodes in L0.")
    catalog = commands.add_parser("catalog", help="Manage the catalog of episodes in L0.")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_commands.add_parser("rebuild", help="Delete the catalog and rebuild it from L0.")
    args = parser.parse_args(argv)

    try:
        data_root = load_data_root()
        if args.command == "ingest":
            _ingest(data_root)
        elif args.command == "catalog":
            _rebuild_catalog(data_root)
    except PaError as error:
        print(f"pa: {error}", file=sys.stderr)
        return 1
    return 0


def _ingest(data_root: DataRoot) -> None:
    owner = load_owner()
    data_root.inbox.mkdir(parents=True, exist_ok=True)
    store = FilesystemL0(data_root.l0)
    with open_catalog(data_root.catalog, store) as catalog:
        summary = ingest(data_root.inbox, store, catalog, owner)
    print(
        f"ingested {summary.ingested}, unchanged {summary.unchanged}, "
        f"quarantined {summary.quarantined}"
    )


def _rebuild_catalog(data_root: DataRoot) -> None:
    count = rebuild_catalog(data_root.catalog, FilesystemL0(data_root.l0))
    print(f"catalog rebuilt: {count} episodes")
