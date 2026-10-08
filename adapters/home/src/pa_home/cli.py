"""The `pa` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pa_core.catalog import open_catalog, rebuild_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest
from pa_core.renormalize import renormalize
from pa_home.agent_project import render_agent_project
from pa_home.backup import back_up
from pa_home.config import (
    DataRoot,
    load_data_root,
    load_owner,
    require_backup_config,
    require_outside_repository,
)
from pa_home.filesystem_l0 import FilesystemL0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa", description="Personal assistant.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="Turn raw payloads in the inbox into episodes in L0.")
    catalog = commands.add_parser("catalog", help="Manage the catalog of episodes in L0.")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_commands.add_parser("rebuild", help="Delete the catalog and rebuild it from L0.")
    commands.add_parser("backup", help="Back up L0 to the configured restic repository.")
    commands.add_parser(
        "renormalize", help="Rebuild every envelope in L0 from its raw payload, then the catalog."
    )
    agent_project = commands.add_parser(
        "agent-project", help="Manage the agent project the assistant runs in."
    )
    agent_project_commands = agent_project.add_subparsers(
        dest="agent_project_command", required=True
    )
    render = agent_project_commands.add_parser(
        "render",
        help="Write a copy of the agent project filled in with the owner and instance paths.",
    )
    render.add_argument("target", type=Path, help="New or empty directory to render into.")
    render.add_argument(
        "--l0", type=Path, required=True, help="The L0 directory the assistant reads."
    )
    render.add_argument(
        "--scratch",
        type=Path,
        required=True,
        help="The only directory the assistant may write to (with sandboxed Bash).",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "agent-project":
            _render_agent_project(args.target, l0=args.l0, scratch=args.scratch)
            return 0
        data_root = load_data_root()
        if args.command == "ingest":
            _ingest(data_root)
        elif args.command == "catalog":
            _rebuild_catalog(data_root)
        elif args.command == "backup":
            _backup(data_root)
        elif args.command == "renormalize":
            _renormalize(data_root)
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


def _backup(data_root: DataRoot) -> None:
    require_backup_config()
    snapshot_id = back_up(data_root.l0)
    print(f"backed up L0 to snapshot {snapshot_id[:8]}")


def _renormalize(data_root: DataRoot) -> None:
    store = FilesystemL0(data_root.l0)
    count = renormalize(store, load_owner())
    # The catalog indexes the old envelopes until it is rebuilt from the new ones.
    rebuild_catalog(data_root.catalog, store)
    print(f"renormalized {count} episodes; catalog rebuilt")


def _render_agent_project(target: Path, *, l0: Path, scratch: Path) -> None:
    owner = load_owner()
    target = target.expanduser().resolve()
    require_outside_repository(
        target,
        "The render target",
        "A rendered agent project names the owner, so it must live outside the repository.",
    )
    render_agent_project(
        target, owner=owner, l0=l0.expanduser().resolve(), scratch=scratch.expanduser().resolve()
    )
    print(f"rendered the agent project into {target}")
