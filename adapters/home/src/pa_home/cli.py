"""The `pa` command."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pa_core.catalog import open_catalog, rebuild_catalog
from pa_core.errors import PaError
from pa_core.ingest import ingest
from pa_core.inspection import FORMATS, format_for, inspect_structure
from pa_core.librarian.replay import BATCH_DAYS, RUN_LOG, ReplayError
from pa_core.librarian.run import LibrarianRunError, RunOutcome
from pa_core.renormalize import renormalize
from pa_core.status import Failure, read_status
from pa_home import librarian
from pa_home.agent_project import render_agent_project
from pa_home.backup import back_up
from pa_home.capture import CycleOutcome, capture_cycle
from pa_home.collectors import Collector
from pa_home.collectors.calendar import BackfillCount, CalendarCollector, count_for_backfill
from pa_home.collectors.calendar_server import CalDavServer, CalendarServer
from pa_home.collectors.gmail import GmailClient, GmailCollector, count_by_category
from pa_home.collectors.vault import VaultCollector
from pa_home.config import (
    GMAIL_CLIENT_VAR,
    GMAIL_TOKEN_VAR,
    CaptureConfig,
    ConfigError,
    DataRoot,
    load_capture_config,
    load_data_root,
    load_gmail_config,
    load_icloud_calendars,
    load_icloud_credentials,
    load_librarian_config,
    load_owner,
    load_time_zone,
    require_outside_repository,
)
from pa_home.cut import take_cut
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.gmail_api import DEFAULT_AUTH_PORT, GoogleGmailClient, authorize
from pa_home.librarian_project import render_librarian_project
from pa_home.lock import LockHeld, data_root_lock, skipped
from pa_home.nightly import nightly_job


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pa", description="Personal assistant.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "ingest",
        help="Turn raw payloads in the inbox into episodes in L0. Skips if another job holds "
        "the data root's lock.",
    )
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
    capture = commands.add_parser(
        "capture",
        help="Run a capture cycle: collect each configured source into the inbox, ingest, and "
        "publish each source's watermark once its payloads have landed. Skips if another job "
        "holds the data root's lock.",
    )
    capture_kind = capture.add_mutually_exclusive_group()
    capture_kind.add_argument(
        "--backfill",
        type=datetime.fromisoformat,
        metavar="WINDOW_START",
        help="Backfill instead: capture each source's history from WINDOW_START on (ISO 8601; "
        "the owner's time zone if it has no offset), such as two months back. Collections then "
        "carry on from the backfill.",
    )
    capture_kind.add_argument(
        "--reconcile",
        action="store_true",
        help="Reconcile instead of collecting, where a source can (iCloud Calendar): compare "
        "everything it holds against what was captured, so a change or deletion a collection "
        "missed still lands. The nightly job's cycle.",
    )
    gmail = commands.add_parser("gmail", help="Set up and check Gmail's capture.")
    gmail_commands = gmail.add_subparsers(dest="gmail_command", required=True)
    gmail_auth = gmail_commands.add_parser(
        "auth",
        help="Ask for your consent to read Gmail (read-only) in a browser, and save the token "
        f"at {GMAIL_TOKEN_VAR}, readable only by you. Also how to re-auth a dead token. On a "
        "headless machine, forward the port over SSH first.",
    )
    gmail_auth.add_argument(
        "--port",
        type=int,
        default=DEFAULT_AUTH_PORT,
        help=f"The local port the consent redirects to (default {DEFAULT_AUTH_PORT}).",
    )
    gmail_dry_run = gmail_commands.add_parser(
        "dry-run",
        help="Count the messages a backfill from WINDOW_START would capture, by category, from "
        "their labels alone, without fetching any message. Prints counts only.",
    )
    gmail_dry_run.add_argument(
        "--window-start",
        type=datetime.fromisoformat,
        required=True,
        help="ISO 8601; the owner's time zone if it has no offset.",
    )
    calendar = commands.add_parser("calendar", help="Set up and check iCloud Calendar's capture.")
    calendar_commands = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_commands.add_parser(
        "calendars",
        help="Name the calendars the iCloud account serves, with what they hold, marking those "
        "PA_ICLOUD_CALENDARS captures.",
    )
    calendar_dry_run = calendar_commands.add_parser(
        "dry-run",
        help="Count, per configured calendar, the resources a backfill from WINDOW_START would "
        "take. Every resource is read; nothing but counts is printed.",
    )
    calendar_dry_run.add_argument(
        "--window-start",
        type=datetime.fromisoformat,
        required=True,
        help="ISO 8601; the owner's time zone if it has no offset.",
    )
    commands.add_parser(
        "nightly",
        help="Run the nightly job under the data root's lock: a capture cycle with "
        "reconciliation, then the librarian run, then the backup, each recorded in the status. "
        "A step that fails doesn't stop the next. Skips if another job holds the lock.",
    )
    commands.add_parser(
        "status",
        help="Print how current the PA is: each source's watermark, cadence, and failures, and "
        "the last ingest, librarian run, and backup. Prints no content.",
    )
    commands.add_parser(
        "cut",
        help="Cut the store now, for real-data evals: every episode captured by now, the wiki "
        "at its last commit, and the status file, under the data root's cuts/. Refused while "
        "another job holds the data root's lock.",
    )
    catalog = commands.add_parser("catalog", help="Manage the catalog of episodes in L0.")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_commands.add_parser("rebuild", help="Delete the catalog and rebuild it from L0.")
    commands.add_parser(
        "backup",
        help="Back up L0, the wiki's git directory, and the private eval directory to the "
        "configured restic repository, and record the outcome in the status. Skips if another "
        "run holds the data root's lock.",
    )
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
    render.add_argument(
        "--status",
        type=Path,
        help="The status file that says how current L0 and the wiki are, in a read-only "
        "directory of its own (default: none).",
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
        "--window-start",
        type=datetime.fromisoformat,
        help="Start the replay at this time (ISO 8601; the owner's time zone if it has no "
        "offset), such as a backfill's window start: the first batch is its week, and nothing "
        "that occurred before it is offered but calendar events still going at it, such as a "
        "series. Else the replay starts at the first episode.",
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
            _render_agent_project(
                args.target, l0=args.l0, scratch=args.scratch, wiki=args.wiki, status=args.status
            )
            return 0
        if args.command == "librarian-project":
            _render_librarian_project(args.target, l0=args.l0, wiki=args.wiki)
            return 0
        if args.command == "inspect":
            return _inspect(args.paths, args.fmt)
        if args.command == "librarian" and args.librarian_command == "replay":
            _replay(
                args.target,
                args.corpus,
                args.as_of,
                args.batch_days,
                window_start=args.window_start,
                resume=args.resume,
            )
            return 0
        if args.command == "gmail" and args.gmail_command == "auth":
            _gmail_auth(args.port)
            return 0
        if args.command == "gmail":
            _gmail_dry_run(args.window_start)
            return 0
        if args.command == "calendar" and args.calendar_command == "calendars":
            _calendars()
            return 0
        if args.command == "calendar":
            return _calendar_dry_run(args.window_start)
        data_root = load_data_root()
        if args.command == "ingest":
            try:
                with data_root_lock(data_root):
                    _ingest(data_root)
            except LockHeld:
                print(skipped("ingest"))
        elif args.command == "capture":
            return _capture(data_root, args.backfill, reconcile=args.reconcile)
        elif args.command == "nightly":
            return _nightly(data_root)
        elif args.command == "status":
            _print_status(data_root)
        elif args.command == "cut":
            _cut(data_root)
        elif args.command == "catalog":
            with data_root_lock(data_root):
                _rebuild_catalog(data_root)
        elif args.command == "backup":
            _backup(data_root)
        elif args.command == "renormalize":
            with data_root_lock(data_root):
                _renormalize(data_root)
        elif args.command == "librarian" and args.librarian_command == "seed":
            with data_root_lock(data_root):
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


def open_gmail(token: Path) -> GmailClient:
    """The owner's Gmail account, read through the token at `token`."""
    return GoogleGmailClient(token)


def open_icloud() -> CalendarServer:
    """The owner's iCloud account, with the Apple ID and app-specific password configured."""
    credentials = load_icloud_credentials()
    return CalDavServer(credentials.username, credentials.password)


def build_collectors(config: CaptureConfig, time_zone: ZoneInfo) -> list[Collector]:
    """A collector for each source `config` configures."""
    collectors: list[Collector] = []
    if config.gmail_token is not None:
        collectors.append(GmailCollector(open_gmail(config.gmail_token)))
    if config.icloud_calendars:
        # Opened by name at each pass, so a missing credential fails the calendar alone.
        collectors.append(
            CalendarCollector(lambda: open_icloud(), config.icloud_calendars, time_zone)
        )
    if config.vault is not None:
        collectors.append(VaultCollector(config.vault.path, config.vault.daily_notes, time_zone))
    return collectors


def _capture(
    data_root: DataRoot, window_start: datetime | None = None, *, reconcile: bool = False
) -> int:
    owner, time_zone, config = load_owner(), load_time_zone(), load_capture_config()
    outcome = capture_cycle(
        data_root,
        collectors=build_collectors(config, time_zone),
        owner=owner,
        cadences=config.cadences,
        window_start=None if window_start is None else _in_time_zone(window_start, time_zone),
        reconcile=reconcile,
    )
    if outcome.skipped:
        print(skipped("capture"))
        return 0
    _report_cycle(outcome, time_zone, "capture")
    return 1 if outcome.failed else 0


def _report_cycle(outcome: CycleOutcome, time_zone: ZoneInfo, name: str) -> None:
    """Print what a capture cycle did, as `name`: counts and reasons, never content."""
    for source, count in outcome.collected.items():
        print(f"{name}: {source}: collected {count}")
    for source, reason in outcome.failures.items():
        print(f"pa: {name}: {source}: collection failed: {reason}", file=sys.stderr)
    if outcome.ingested is not None:
        summary = outcome.ingested
        print(
            f"{name}: ingested {summary.ingested}, unchanged {summary.unchanged}, "
            f"quarantined {summary.quarantined}"
            + (f", left {len(summary.left)}" if summary.left else "")
        )
        for reason in summary.left:
            print(f"pa: {name}: {reason}", file=sys.stderr)
    if outcome.ingest_failure is not None:
        print(f"pa: {name}: ingest failed: {outcome.ingest_failure}", file=sys.stderr)
    for source, watermark in outcome.published.items():
        print(f"{name}: {source}: watermark {_moment(watermark, time_zone)}")


def _nightly(data_root: DataRoot) -> int:
    owner, time_zone, config = load_owner(), load_time_zone(), load_capture_config()
    outcome = nightly_job(
        data_root,
        collectors=build_collectors(config, time_zone),
        owner=owner,
        cadences=config.cadences,
        time_zone=time_zone,
    )
    if outcome.skipped:
        print(skipped("nightly"))
        return 0
    if outcome.capture is not None:
        _report_cycle(outcome.capture, time_zone, "nightly: capture")
    if outcome.capture_failure is not None:
        print(f"pa: nightly: capture failed: {outcome.capture_failure}", file=sys.stderr)
    if outcome.librarian is not None:
        _report_run(outcome.librarian, "nightly: librarian")
    if outcome.librarian_failure is not None:
        print(f"pa: nightly: librarian: {outcome.librarian_failure}", file=sys.stderr)
    if outcome.snapshot is not None:
        print(f"nightly: backup: took snapshot {outcome.snapshot[:8]}")
    if outcome.backup_failure is not None:
        print(f"pa: nightly: backup failed: {outcome.backup_failure}", file=sys.stderr)
    return 1 if outcome.failed else 0


def _gmail_auth(port: int) -> None:
    config = load_gmail_config()
    if config.client is None:
        raise ConfigError(
            f"{GMAIL_CLIENT_VAR} is not set: set it to the OAuth client (a Desktop app client's "
            "JSON) downloaded from your Cloud project, such as ~/.config/pa/gmail-client.json."
        )
    authorize(config.client, config.token, port=port)
    print("gmail: consent given; the token is saved, readable only by you")


def _gmail_dry_run(window_start: datetime) -> None:
    time_zone = load_time_zone()
    start = _in_time_zone(window_start, time_zone)
    counts = count_by_category(open_gmail(load_gmail_config().token), start, datetime.now(UTC))
    by_category = ", ".join(f"{category} {count}" for category, count in counts.items())
    print(
        f"gmail: {counts.total()} messages since {start.astimezone(time_zone).date()}: "
        f"{by_category}"
    )


def _calendars() -> None:
    configured = set(load_icloud_calendars(required=False))
    for collection in open_icloud().collections():
        name = collection.name or "(no name)"
        holds = "/".join(collection.components) or "unknown components"
        captured = ", captured" if collection.name in configured else ""
        print(f"calendar: {name} ({holds}){captured}")


def _calendar_dry_run(window_start: datetime) -> int:
    time_zone = load_time_zone()
    calendars = load_icloud_calendars()
    start = _in_time_zone(window_start, time_zone)
    day = start.astimezone(time_zone).date()
    counts = count_for_backfill(open_icloud(), calendars, start)
    status, resources, taken = 0, 0, 0
    for name, count in counts.items():
        if not isinstance(count, BackfillCount):
            print(f"pa: calendar: {name}: {count}", file=sys.stderr)
            status = 1
            continue
        print(
            f"calendar: {name}: {count.resources} resources: {count.in_window} with an "
            f"occurrence since {day}, {count.before} before, {count.unreadable} unreadable"
        )
        resources += count.resources
        taken += count.in_window + count.unreadable
    print(f"calendar: a backfill from {day} takes {taken} of {resources} resources")
    return status


def _in_time_zone(moment: datetime, time_zone: ZoneInfo) -> datetime:
    """`moment`, taken in `time_zone` when it has no offset of its own."""
    if moment.utcoffset() is None:
        return moment.replace(tzinfo=time_zone)
    return moment


def _print_status(data_root: DataRoot) -> None:
    time_zone = load_time_zone()
    if not data_root.status.exists():
        print("status: nothing recorded yet")
        return
    status = read_status(data_root.status)
    now = datetime.now(UTC)

    def moment(value: datetime | None) -> str:
        return "never" if value is None else _moment(value, time_zone)

    def failure(value: Failure | None) -> str:
        return "none" if value is None else f"{moment(value.at)} ({value.reason})"

    print(f"status: updated {moment(status.updated_at)}")
    for name, source in sorted(status.sources.items()):
        stale = source.watermark is None or now - source.watermark > source.cadence
        print(
            f"{name}: watermark {moment(source.watermark)}"
            f"{' (stale)' if stale else ''}, "
            f"expected every {source.cadence.total_seconds() / 60:g} min; "
            f"last attempt {moment(source.last_attempt)}; "
            f"last failure {failure(source.last_failure)}; "
            f"quarantined {source.quarantined}"
        )
    print(
        f"ingest: last success {moment(status.ingest.last_success)}; "
        f"last failure {failure(status.ingest.last_failure)}"
    )
    run = status.wiki.last_run
    if run is None:
        last_run = "never"
    else:
        cost = "" if run.cost_usd is None else f", cost ${run.cost_usd:.2f}"
        warnings = "" if run.warnings is None else f", {run.warnings} lint warnings"
        outcome = "succeeded" if run.succeeded else f"failed ({run.reason})"
        last_run = f"{moment(run.at)} {outcome}{cost}{warnings}"
    print(f"wiki: current through {moment(status.wiki.current_through)}; last run {last_run}")
    print(
        f"backup: last success {moment(status.backup.last_success)}; "
        f"last failure {failure(status.backup.last_failure)}"
    )


def _moment(value: datetime, time_zone: ZoneInfo) -> str:
    return value.astimezone(time_zone).strftime("%Y-%m-%d %H:%M %Z")


def _cut(data_root: DataRoot) -> None:
    with data_root_lock(data_root):
        cut = take_cut(data_root, now=datetime.now(UTC))
    wiki = "no wiki" if cut.manifest.wiki is None else f"wiki at {cut.manifest.wiki.commit[:12]}"
    status = "status file" if cut.manifest.status else "no status file"
    print(f"cut: {cut.path}")
    print(f"cut: {cut.manifest.episodes} episodes, {wiki}, {status}")


def _rebuild_catalog(data_root: DataRoot) -> None:
    count = rebuild_catalog(data_root.catalog, FilesystemL0(data_root.l0))
    print(f"catalog rebuilt: {count} episodes")


def _backup(data_root: DataRoot) -> None:
    try:
        with data_root_lock(data_root):
            snapshot_id = back_up(data_root)
    except LockHeld:
        print(skipped("backup"))
        return
    print(f"backup: took snapshot {snapshot_id[:8]}")


def _renormalize(data_root: DataRoot) -> None:
    store = FilesystemL0(data_root.l0)
    count = renormalize(store, load_owner())
    # The catalog indexes the old envelopes until it is rebuilt from the new ones.
    rebuild_catalog(data_root.catalog, store)
    print(f"renormalized {count} episodes; catalog rebuilt")


def _run_librarian(data_root: DataRoot) -> None:
    owner, time_zone, config = load_owner(), load_time_zone(), load_librarian_config()
    try:
        with data_root_lock(data_root):
            outcome = librarian.run(data_root, owner=owner, time_zone=time_zone, config=config)
    except LockHeld:
        print(skipped("librarian"))
        return
    _report_run(outcome, "librarian")


def _report_run(outcome: RunOutcome, name: str) -> None:
    """Print what a librarian run did, as `name`: counts, never content."""
    if outcome.batch is None or outcome.commit is None:
        print(f"{name}: no new episodes since the last run")
        return
    batch = outcome.batch
    print(
        f"{name}: offered {len(batch.episodes)} of {batch.captured} new episodes; "
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
    window_start: datetime | None,
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
    if window_start is not None and window_start.utcoffset() is None:
        window_start = window_start.replace(tzinfo=time_zone)

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
            window_start=window_start,
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


def _render_agent_project(
    target: Path, *, l0: Path, scratch: Path, wiki: Path | None, status: Path | None
) -> None:
    owner = load_owner()
    target = _render_target(target, "agent project")
    render_agent_project(
        target,
        owner=owner,
        l0=l0.expanduser().resolve(),
        scratch=scratch.expanduser().resolve(),
        wiki=wiki.expanduser().resolve() if wiki else None,
        status=status.expanduser().resolve() if status else None,
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
