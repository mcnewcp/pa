"""The `pa` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath

from pa_core.catalog import open_catalog, rebuild_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest
from pa_core.inspection import FORMATS, format_for, inspect_structure
from pa_core.librarian.replay import BATCH_DAYS, RUN_LOG, ReplayError
from pa_core.librarian.run import LibrarianRunError, RunOutcome
from pa_core.renormalize import renormalize
from pa_home import librarian
from pa_home.agent_project import render_agent_project
from pa_home.backup import back_up
from pa_home.config import (
    DataRoot,
    load_data_root,
    load_librarian_config,
    load_owner,
    load_time_zone,
    require_backup_config,
    require_outside_repository,
)
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.librarian_project import render_librarian_project


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa", description="Personal assistant.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest", help="Turn raw payloads in the inbox into episodes in L0.")
    inspect = commands.add_parser(
        "inspect",
        help="Print the structure of a payload, an envelope, or a quarantine record, never its "
        "content: names, kinds, counts, and sizes.",
    )
    inspect.add_argument("paths", type=Path, nargs="+", metavar="PATH", help="A file to inspect.")
    inspect.add_argument(
        "--as",
        dest="fmt",
        choices=FORMATS,
        help="Read each file as this format, whatever its suffix (.eml, .ics, .md, .json).",
    )
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
        help="The only directory the assistant may write to.",
    )
    render.add_argument(
        "--wiki", type=Path, help="The wiki the assistant reads beside L0 (default: none)."
    )
    librarian_project = commands.add_parser(
        "librarian-project", help="Manage the librarian project the librarian's sessions run in."
    )
    librarian_project_commands = librarian_project.add_subparsers(
        dest="librarian_project_command", required=True
    )
    render_librarian = librarian_project_commands.add_parser(
        "render",
        help="Write a copy of the librarian project filled in with the owner and the paths "
        "the sessions see L0 and the wiki at, such as for a read-only mount in the "
        "librarian's VM.",
    )
    render_librarian.add_argument(
        "target", type=Path, help="New or empty directory to render into."
    )
    render_librarian.add_argument(
        "--l0",
        type=PurePosixPath,
        required=True,
        help="Where the sessions see L0 (an absolute path, as the session sees it).",
    )
    render_librarian.add_argument(
        "--wiki",
        type=PurePosixPath,
        required=True,
        help="Where the sessions see the wiki (an absolute path, as the session sees it).",
    )
    librarian_parser = commands.add_parser("librarian", help="Keep the wiki over L0.")
    librarian_commands = librarian_parser.add_subparsers(dest="librarian_command", required=True)
    librarian_commands.add_parser(
        "run",
        help="Observe the episodes captured since the last run into the wiki, and fold them "
        "into pages.",
    )
    replay = librarian_commands.add_parser(
        "replay",
        help="Rebuild a wiki in a new directory by replaying L0 through the librarian, a batch "
        "of days at a time.",
    )
    replay.add_argument(
        "target",
        type=Path,
        help="New or empty directory for the wiki (wiki/), its git directory (wiki.git/), and "
        f"the run log ({RUN_LOG}).",
    )
    replay.add_argument(
        "--corpus",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="Replay these raw payloads (one directory per source, as in the inbox), ingested "
        "into a throwaway data root, instead of the data root's L0. Repeat for a corpus in "
        "several directories.",
    )
    replay.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        help="Replay up to this time (ISO 8601; the owner's time zone if it has no offset), "
        "else up to the latest episode. A corpus is captured at this time.",
    )
    replay.add_argument(
        "--batch-days",
        type=int,
        default=BATCH_DAYS,
        help=f"How many days each batch covers (default {BATCH_DAYS}).",
    )
    replay.add_argument(
        "--resume",
        action="store_true",
        help="Carry on with the replay in TARGET from its last committed batch.",
    )
    seed = librarian_commands.add_parser(
        "seed",
        help="Start the data root's fresh wiki as a copy of an eval wiki replayed over the "
        "same episodes (such as after reseeding the data root with that corpus), so the next "
        "run takes only what's captured after.",
    )
    seed.add_argument(
        "eval_wiki",
        type=Path,
        metavar="EVAL_WIKI",
        help="The eval wiki: a directory holding the replay's wiki/ (and its run log), such as "
        "evals/wikis/scale/<librarian version>.",
    )
    seed.add_argument(
        "--captured-through",
        type=datetime.fromisoformat,
        help="Count the episodes captured up to this time (ISO 8601; the owner's time zone if "
        "it has no offset) as in the wiki, else all of L0: the reseed's capture time.",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "agent-project":
            _render_agent_project(args.target, l0=args.l0, scratch=args.scratch, wiki=args.wiki)
            return 0
        if args.command == "librarian-project":
            _render_librarian_project(args.target, l0=args.l0, wiki=args.wiki)
            return 0
        if args.command == "inspect":
            return _inspect(args.paths, args.fmt)
        if args.command == "librarian" and args.librarian_command == "replay":
            _replay(args.target, args.corpus, args.as_of, args.batch_days, resume=args.resume)
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
        elif args.command == "librarian" and args.librarian_command == "seed":
            _seed(data_root, args.eval_wiki, args.captured_through)
        elif args.command == "librarian":
            _run_librarian(data_root)
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
    left = f", left {len(summary.left)}" if summary.left else ""
    print(
        f"ingested {summary.ingested}, unchanged {summary.unchanged}, "
        f"quarantined {summary.quarantined}{left}"
    )
    if summary.left:
        for reason in summary.left:
            print(f"pa: {reason}", file=sys.stderr)
        raise PaError(f"left {len(summary.left)} in the inbox: this user can't remove them")


def _inspect(paths: list[Path], fmt: str | None) -> int:
    """Print each file's structure. A file's path is never printed: a glob may have named it,
    and a name in the store can be content."""
    status = 0
    for number, path in enumerate(paths, start=1):
        if number > 1:
            print()
        if len(paths) > 1:
            print(f"file {number} of {len(paths)}")
        try:
            raw = path.read_bytes()
        except OSError as error:
            print(f"pa: file {number} can't be read ({error.strerror})", file=sys.stderr)
            status = 1
            continue
        print(inspect_structure(raw, fmt or format_for(path.suffix)), end="")
    return status


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


def _run_librarian(data_root: DataRoot) -> None:
    outcome = librarian.run(
        data_root,
        owner=load_owner(),
        time_zone=load_time_zone(),
        config=load_librarian_config(),
    )
    if outcome.batch is None or outcome.commit is None:
        print("librarian: no new episodes since the last run")
        return
    batch = outcome.batch
    print(
        f"librarian: offered {len(batch.episodes)} of {batch.captured} new episodes; "
        f"committed {outcome.commit[:8]}; {len(outcome.warnings)} lint warnings"
    )


def _seed(data_root: DataRoot, eval_wiki: Path, captured_through: datetime | None) -> None:
    if captured_through is not None and captured_through.utcoffset() is None:
        captured_through = captured_through.replace(tzinfo=load_time_zone())
    commit = librarian.seed(
        data_root,
        eval_wiki.expanduser().resolve(),
        owner=load_owner(),
        captured_through=captured_through,
    )
    print(f"librarian: seeded the wiki from {eval_wiki}; committed {commit[:8]}")


def _replay(
    target: Path,
    corpus: list[Path],
    as_of: datetime | None,
    batch_days: int,
    *,
    resume: bool,
) -> None:
    owner, time_zone = load_owner(), load_time_zone()
    target = target.expanduser().resolve()
    data_root = None
    if not corpus:
        data_root = load_data_root()
        require_outside_repository(
            target,
            "The replay target",
            "A wiki replayed from the data root holds the owner's data, so it must live "
            "outside the repository.",
        )
    if as_of is not None and as_of.utcoffset() is None:
        as_of = as_of.replace(tzinfo=time_zone)

    def report(outcome: RunOutcome) -> None:
        batch = outcome.batch
        if batch is None or batch.window is None or outcome.commit is None:
            return
        print(
            f"librarian replay: {batch.window.start.astimezone(time_zone):%Y-%m-%d} to "
            f"{batch.window.end.astimezone(time_zone):%Y-%m-%d}: offered "
            f"{len(batch.episodes)} of {batch.captured} episodes; committed {outcome.commit[:8]}"
        )

    try:
        outcome = librarian.replay(
            target,
            data_root=data_root,
            corpus=[directory.expanduser().resolve() for directory in corpus],
            owner=owner,
            time_zone=time_zone,
            config=load_librarian_config(),
            batch_days=batch_days,
            as_of=as_of,
            resume=resume,
            progress=report,
        )
    except ReplayError:
        raise
    except LibrarianRunError as error:
        raise LibrarianRunError(
            f"{error}\nThe replay stopped; carry on from its last committed batch with --resume."
        ) from error
    print(f"librarian replay: committed {len(outcome.runs)} batches into {target}")


def _render_target(target: Path, project: str) -> Path:
    """`target`, resolved, once it's known to be outside the repository: a rendered project
    names the owner."""
    target = target.expanduser().resolve()
    require_outside_repository(
        target,
        "The render target",
        f"A rendered {project} names the owner, so it must live outside the repository.",
    )
    return target


def _render_agent_project(target: Path, *, l0: Path, scratch: Path, wiki: Path | None) -> None:
    owner = load_owner()
    target = _render_target(target, "agent project")
    render_agent_project(
        target,
        owner=owner,
        l0=l0.expanduser().resolve(),
        scratch=scratch.expanduser().resolve(),
        wiki=wiki.expanduser().resolve() if wiki else None,
    )
    print(f"rendered the agent project into {target}")


def _render_librarian_project(target: Path, *, l0: PurePosixPath, wiki: PurePosixPath) -> None:
    for name, path in (("--l0", l0), ("--wiki", wiki)):
        if not path.is_absolute():
            raise PaError(
                f"{name} must be an absolute path, as the librarian's session sees it (got {path})"
            )
    owner = load_owner()
    target = _render_target(target, "librarian project")
    render_librarian_project(target, owner=owner, l0=l0, wiki=wiki)
    print(f"rendered the librarian project into {target}")
