"""The nightly job (`pa nightly`) and the one lock on the data root, over a temporary data root
and vault, with the librarian's stand-in `claude` (conftest.py) and a stand-in `restic`.

The nightly job holds the data root's lock throughout: a capture cycle with reconciliation, then
the librarian run, then the backup. Tests assert on what it leaves behind: L0, what the
librarian's sessions were offered, the wiki, what the backup was asked to snapshot, and the
status file.
"""

import json
import stat
import subprocess
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.envelope import Source
from pa_core.owner import Owner
from pa_core.status import AssistantLogin, Status, read_status, write_status
from pa_home.capture import capture_cycle
from pa_home.cli import main
from pa_home.collectors import SourceInbox
from pa_home.collectors.vault import VaultCollector
from pa_home.config import DataRoot
from pa_home.git_wiki import GitWikiRepository
from pa_home.lock import data_root_lock
from pa_home.nightly import HELD_BACK, NightlyOutcome, nightly_job

CHICAGO = ZoneInfo("America/Chicago")
OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
CADENCES = {Source.OBSIDIAN: timedelta(minutes=15)}

# Records what it was asked to back up, and the wiki's last commit as it found it, then reports
# a snapshot as `restic backup --json` does.
STAND_IN_RESTIC = """\
import json, os, subprocess, sys
head = subprocess.run(["git", "--git-dir=wiki.git", "rev-parse", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
with open(os.environ["RESTIC_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "wiki_head": head}) + "\\n")
print(json.dumps({"message_type": "summary", "snapshot_id": "5d3f0a9b" + "0" * 56}))
"""


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stand_in_claude: Path) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_LIBRARIAN_COMMAND", str(stand_in_claude))
    for var in (
        "PA_LIBRARIAN_SANDBOX",
        "PA_LIBRARIAN_DREAMING_INTERVAL_DAYS",
        "PA_GMAIL_TOKEN",
        "PA_ICLOUD_CALENDARS",
        "PA_ASSISTANT_LOGIN_EXPIRES",
    ):
        monkeypatch.delenv(var, raising=False)
    restic = tmp_path / "bin" / "restic"
    restic.parent.mkdir()
    restic.write_text(f"#!{sys.executable}\n{STAND_IN_RESTIC}")
    restic.chmod(restic.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{restic.parent}:/usr/bin:/bin")
    monkeypatch.setenv("RESTIC_LOG", str(tmp_path / "restic.jsonl"))
    monkeypatch.setenv("RESTIC_REPOSITORY", str(tmp_path / "repository"))
    password = tmp_path / "restic.password"
    password.write_text("correct horse battery staple\n")
    password.chmod(0o600)
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", str(password))
    for var in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND", "RESTIC_REPOSITORY_FILE"):
        monkeypatch.delenv(var, raising=False)
    return root


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vault"
    (path / "Daily").mkdir(parents=True)
    monkeypatch.setenv("PA_VAULT_DIR", str(path))
    monkeypatch.setenv("PA_VAULT_DAILY_NOTES", "Daily")
    return path


def write_note(vault: Path, day: str, text: str) -> None:
    (vault / "Daily" / f"{day}.md").write_text(text)


def sessions(tmp_path: Path, phase: str = "observation") -> list[dict]:
    log = tmp_path / "sessions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [record for record in records if record["phase"] == phase]


def offered_days(tmp_path: Path) -> list[list[str]]:
    """The days of the daily notes each observation session was offered, in order."""
    return [
        [episode["day"] for episode in session["episodes"] if episode["kind"] == "note"]
        for session in sessions(tmp_path)
    ]


def backups(tmp_path: Path) -> list[dict]:
    log = tmp_path / "restic.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def wiki_head(data_root: Path) -> str:
    return subprocess.run(
        ["git", f"--git-dir={data_root / 'wiki.git'}", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_the_nightly_job_captures_then_runs_the_librarian_then_backs_up_the_nights_wiki(
    data_root: Path, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 0

    # The note its cycle collected is offered by its run.
    assert offered_days(tmp_path) == [["2020-01-01"]]
    # The backup came after the run: it holds the night's wiki.
    [backup] = backups(tmp_path)
    assert backup["wiki_head"] == wiki_head(data_root)
    assert {"l0", "wiki.git"} <= set(backup["args"])
    status = read_status(data_root / "status" / "status.json")
    run, obsidian = status.wiki.last_run, status.sources[Source.OBSIDIAN]
    assert run is not None and run.succeeded
    assert obsidian.last_attempt is not None and status.backup.last_success is not None
    assert obsidian.last_attempt <= run.at <= status.backup.last_success
    out = capsys.readouterr().out
    assert "nightly: capture: obsidian: collected 1" in out
    assert "nightly: librarian: offered 1 of 1 new episodes" in out
    assert "nightly: backup: took snapshot 5d3f0a9b" in out


class StartsTheLibrarianMidCycle:
    """The vault's collector, and then, before the cycle's ingest, `pa librarian run` started
    beside it, as a timer or the owner might."""

    source = Source.OBSIDIAN

    def __init__(self, vault: Path) -> None:
        self._vault = VaultCollector(vault, "Daily", CHICAGO)
        self.librarian_exit: int | None = None

    def collect(self, inbox: SourceInbox, state: Path, *, now) -> None:
        self._vault.collect(inbox, state, now=now)
        self.librarian_exit = main(["librarian", "run"])

    def backfill(self, inbox: SourceInbox, state: Path, *, window_start, now) -> None:
        raise AssertionError("not a backfill")


HOTEL = (
    b"X-Gmail-Labels: Inbox\r\n"
    b"Message-ID: <wedding-0001@example.net>\r\n"
    b"Date: Mon, 5 Oct 2026 21:30:00 -0500\r\n"
    b"From: David Kim <dkim@example.net>\r\n"
    b"To: Argus McNevans <argus@example.com>\r\n"
    b"Subject: Hotel block\r\n"
    b"\r\n"
    b"The hotel block closes on Oct 9.\r\n"
)


def test_a_note_collected_before_a_run_is_offered_by_that_run(
    data_root: Path, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """The known gap: a note is stamped with the time it was collected, so a run that moved its
    cursor past that time (here to mail ingested since) before the note was ingested never
    offered it. A cycle holds the lock from collection through ingest, so a run can't start in
    between: it skips, and the next run offers the note."""
    (data_root / "inbox" / "gmail").mkdir(parents=True)
    (data_root / "inbox" / "gmail" / "hotel.eml").write_bytes(HOTEL)
    assert main(["ingest"]) == 0  # captured now, after the note below
    write_note(vault, "2026-10-05", "## Plans\nSwim on Saturdays.\n")
    collector = StartsTheLibrarianMidCycle(vault)
    capsys.readouterr()

    outcome = capture_cycle(
        DataRoot(data_root),
        collectors=[collector],
        owner=OWNER,
        cadences=CADENCES,
        now=lambda: datetime(2026, 10, 6, 1, 0, tzinfo=CHICAGO),
    )

    assert collector.librarian_exit == 0
    assert "librarian: skipped: another job holds the data root's lock" in capsys.readouterr().out
    assert sessions(tmp_path) == []
    assert outcome.ingested is not None and outcome.ingested.ingested == 1

    assert main(["librarian", "run"]) == 0

    [session] = sessions(tmp_path)
    assert sorted(episode["kind"] for episode in session["episodes"]) == ["email", "note"]


def test_pa_ingest_skips_while_another_run_holds_the_lock(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    (data_root / "inbox" / "gmail").mkdir(parents=True)
    (data_root / "inbox" / "gmail" / "hotel.eml").write_bytes(HOTEL)

    with data_root_lock(DataRoot(data_root)):
        assert main(["ingest"]) == 0

    assert capsys.readouterr().out == "ingest: skipped: another job holds the data root's lock\n"
    assert (data_root / "inbox" / "gmail" / "hotel.eml").exists()
    assert not (data_root / "l0").exists()


@pytest.mark.parametrize(
    "command", [["renormalize"], ["catalog", "rebuild"], ["librarian", "seed", "eval-wiki"]]
)
def test_what_rewrites_the_store_by_hand_is_refused_while_another_run_holds_the_lock(
    data_root: Path, command: list[str], capsys: pytest.CaptureFixture[str]
):
    (data_root / "l0").mkdir(parents=True)

    with data_root_lock(DataRoot(data_root)):
        assert main(command) == 1

    assert "another job holds the data root's lock" in capsys.readouterr().err
    assert not (data_root / "catalog").exists()
    assert not (data_root / "wiki.git").exists()


def status(data_root: Path):
    return read_status(data_root / "status" / "status.json")


def test_the_nightly_job_skips_while_another_run_holds_the_lock(
    data_root: Path, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    with data_root_lock(DataRoot(data_root)):
        assert main(["nightly"]) == 0

    assert capsys.readouterr().out == "nightly: skipped: another job holds the data root's lock\n"
    assert not (data_root / "inbox").exists()
    assert sessions(tmp_path) == [] and backups(tmp_path) == []
    assert not (data_root / "status").exists()


class Calendar:
    """A collector that reconciles, recording which it was asked to do."""

    source = Source.ICLOUD_CALENDAR

    def __init__(self) -> None:
        self.asked: list[str] = []

    def collect(self, inbox: SourceInbox, state: Path, *, now) -> None:
        self.asked.append("collect")

    def reconcile(self, inbox: SourceInbox, state: Path, *, now) -> None:
        self.asked.append("reconcile")

    def backfill(self, inbox: SourceInbox, state: Path, *, window_start, now) -> None:
        self.asked.append("backfill")


def test_the_nightly_cycle_reconciles_what_can_be_reconciled(data_root: Path, vault: Path):
    calendar = Calendar()
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    outcome = nightly_job(
        DataRoot(data_root),
        collectors=[calendar, VaultCollector(vault, "Daily", CHICAGO)],
        owner=OWNER,
        cadences={**CADENCES, Source.ICLOUD_CALENDAR: timedelta(minutes=15)},
        time_zone=CHICAGO,
    )

    assert calendar.asked == ["reconcile"]
    assert outcome.capture is not None and outcome.capture.collected[Source.OBSIDIAN] == 1
    assert not outcome.failed


def test_a_failed_librarian_run_is_recorded_and_the_backup_still_runs(
    data_root: Path, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")
    (tmp_path / "script.json").write_text(
        json.dumps({"observation": [{"fail": "API Error: overloaded"}]})
    )

    assert main(["nightly"]) == 1

    run = status(data_root).wiki.last_run
    assert run is not None and not run.succeeded
    assert run.reason == "the run failed in its observation session and was discarded"
    assert len(backups(tmp_path)) == 1
    assert status(data_root).backup.last_success is not None
    output = capsys.readouterr()
    assert (
        "pa: nightly: librarian: the run failed in its observation session and was discarded"
        in output.err
    )
    assert "nightly: backup: took snapshot 5d3f0a9b" in output.out
    assert "Swim" not in output.out + output.err + (data_root / "status/status.json").read_text()


def test_a_failed_ingest_holds_the_librarian_back_and_the_backup_still_runs(
    data_root: Path, vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """What the cycle failed to ingest waits in the inbox with its capture time, which a run
    would move its cursor past."""
    (data_root / "inbox" / "gmail").mkdir(parents=True)
    (data_root / "inbox" / "gmail" / "hotel.eml").write_bytes(HOTEL)
    assert main(["ingest"]) == 0
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")
    catalog = data_root / "catalog" / "catalog.sqlite"
    catalog.unlink()
    catalog.mkdir()  # so the catalog can't open

    assert main(["nightly"]) == 1

    assert status(data_root).ingest.last_failure is not None
    run = status(data_root).wiki.last_run
    assert run is not None and not run.succeeded and run.reason == HELD_BACK
    assert sessions(tmp_path) == []
    assert len(backups(tmp_path)) == 1
    assert f"pa: nightly: librarian: {HELD_BACK}" in capsys.readouterr().err


def test_a_librarian_without_its_configuration_is_recorded_as_a_failed_run(
    data_root: Path, vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_MAX_TURNS", "none")
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 1

    run = status(data_root).wiki.last_run
    assert run is not None and not run.succeeded and "PA_LIBRARIAN_MAX_TURNS" in str(run.reason)
    assert len(backups(tmp_path)) == 1


def test_a_failed_backup_is_recorded_after_the_nights_run(
    data_root: Path,
    vault: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv("RESTIC_REPOSITORY")
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 1

    assert offered_days(tmp_path) == [["2020-01-01"]]
    failure = status(data_root).backup.last_failure
    assert failure is not None and "RESTIC_REPOSITORY" in failure.reason
    assert f"pa: nightly: backup failed: {failure.reason}" in capsys.readouterr().err


class SeesTheStatus:
    """A collector that reads the status file as the assistant would, mid-night."""

    source = Source.ICLOUD_CALENDAR

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.running_since: datetime | None = None

    def collect(self, inbox: SourceInbox, state: Path, *, now) -> None:
        raise AssertionError("the nightly job reconciles")

    def reconcile(self, inbox: SourceInbox, state: Path, *, now) -> None:
        self.running_since = status(self.data_root).nightly.running_since

    def backfill(self, inbox: SourceInbox, state: Path, *, window_start, now) -> None:
        raise AssertionError("not a backfill")


def test_the_status_says_the_nightly_job_is_running_from_its_start_until_it_ends(
    data_root: Path, vault: Path
):
    started = datetime(2026, 10, 21, 3, 0, tzinfo=CHICAGO)
    calendar = SeesTheStatus(data_root)

    nightly_job(
        DataRoot(data_root),
        collectors=[calendar],
        owner=OWNER,
        cadences={Source.ICLOUD_CALENDAR: timedelta(minutes=15)},
        time_zone=CHICAGO,
        now=lambda: started,
    )

    assert calendar.running_since == started
    assert status(data_root).nightly.running_since is None


def test_the_nightly_job_records_when_the_assistants_login_ends(
    data_root: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
):
    # nightly.sh reads it from the assistant's VM: the time its refresh token stops working.
    monkeypatch.setenv("PA_ASSISTANT_LOGIN_EXPIRES", "2026-12-06T17:53:00+00:00")
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 0

    login = status(data_root).assistant_login
    assert login is not None
    assert login.expires_at == datetime(2026, 12, 6, 17, 53, tzinfo=UTC)


def test_the_nightly_job_records_a_logged_out_assistant(
    data_root: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_ASSISTANT_LOGIN_EXPIRES", "none")
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 0

    login = status(data_root).assistant_login
    assert login is not None and login.expires_at is None


@pytest.mark.parametrize("given", [None, "next month"])
def test_a_night_that_cant_tell_when_the_login_ends_keeps_what_was_recorded(
    data_root: Path, vault: Path, monkeypatch: pytest.MonkeyPatch, given: str | None
):
    # The VM didn't answer (nothing given), or what came back isn't a time.
    if given is not None:
        monkeypatch.setenv("PA_ASSISTANT_LOGIN_EXPIRES", given)
    checked = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    before = AssistantLogin(checked_at=checked, expires_at=checked + timedelta(days=29))
    write_status(DataRoot(data_root).status, Status(assistant_login=before), at=checked)
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")

    assert main(["nightly"]) == 0

    assert status(data_root).assistant_login == before


# The dreaming step: between the librarian run and the backup, on a night whose librarian run
# succeeded, once the last dreaming run is the interval old.

INTERVAL = "PA_LIBRARIAN_DREAMING_INTERVAL_DAYS"
DISMISSING = [{"outcomes": [{"candidate": "c01", "outcome": "dismissed"}]}]
"""What a dreaming session does to dismiss its one candidate."""


def kitchen_page(episode: str) -> str:
    """A page about the kitchen that nothing links to: an orphan, so a candidate."""
    updated = (datetime.now(CHICAGO).date() - timedelta(days=10)).isoformat()
    return (
        "---\ntype: topic\naliases: []\nstatus: active\nsummary: The kitchen remodel.\n"
        f"updated: {updated}\n---\n# Kitchen\n\n## Current state\n"
        f"Waiting on a quote [ep:{episode}].\n\n## Decisions\n\n## Open loops\n\n## Notes\n"
    )


def all_sessions(tmp_path: Path) -> list[dict]:
    log = tmp_path / "sessions.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def script(tmp_path: Path, **phases: list[dict]) -> None:
    (tmp_path / "script.json").write_text(json.dumps(phases))


@pytest.fixture
def orphan(data_root: Path, vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A wiki a first night built, with dreaming off, to which a page that is a candidate has
    been added since. The dreaming interval is then the default. Returns the id of the episode
    the page cites."""
    monkeypatch.setenv(INTERVAL, "0")
    write_note(vault, "2020-01-01", "## Plans\nSwim on Saturdays.\n")
    assert main(["nightly"]) == 0
    [episode] = sessions(tmp_path)[0]["episodes"]
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    (data_root / "wiki" / "topics").mkdir(exist_ok=True)
    (data_root / "wiki" / "topics" / "kitchen.md").write_text(kitchen_page(episode["id"]))
    repository.commit(repository.last_commit_message() or "")
    monkeypatch.delenv(INTERVAL)
    return episode["id"]


def head_message(data_root: Path) -> str:
    return subprocess.run(
        ["git", f"--git-dir={data_root / 'wiki.git'}", "log", "-1", "--format=%B"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_nightly_job_dreams_after_the_librarian_run_and_the_backup_holds_what_it_did(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None, capsys: pytest.CaptureFixture[str]
):
    script(tmp_path, dreaming=DISMISSING)
    capsys.readouterr()

    assert main(["nightly"]) == 0

    [dreaming] = sessions(tmp_path, "dreaming")
    assert [candidate["kind"] for candidate in dreaming["candidates"]] == ["orphan"]
    # The backup came after the dreaming run: it holds its commit.
    assert head_message(data_root).startswith("dreaming run: 1 candidates")
    assert backups(tmp_path)[-1]["wiki_head"] == wiki_head(data_root)
    wiki, backup = status(data_root).wiki, status(data_root).backup
    assert wiki.last_run is not None and wiki.last_dreaming is not None
    assert wiki.last_dreaming.succeeded and wiki.last_dreaming.candidates == 1
    assert backup.last_success is not None
    assert wiki.last_run.at <= wiki.last_dreaming.at <= backup.last_success
    out = capsys.readouterr().out
    assert "nightly: dreaming: judged 1 candidates (0 applied, 0 proposed, 1 dismissed)" in out


FIRST_NIGHT = datetime.combine(
    datetime.now(CHICAGO).date() + timedelta(days=1), time(3, 10), CHICAGO
)
"""When the first night with dreaming on starts: after everything the `orphan` fixture did."""


def night(data_root: Path, vault: Path, at: datetime) -> NightlyOutcome:
    """The nightly job, started at `at`."""
    return nightly_job(
        DataRoot(data_root),
        collectors=[VaultCollector(vault, "Daily", CHICAGO)],
        owner=OWNER,
        cadences=CADENCES,
        time_zone=CHICAGO,
        now=lambda: at,
    )


def dreamed_at(data_root: Path) -> datetime | None:
    """When the status says dreaming last ran."""
    run = status(data_root).wiki.last_dreaming
    return None if run is None else run.at


def test_dreaming_is_due_once_the_last_dreaming_run_is_seven_days_old_by_the_calendar(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None
):
    write_note(vault, "2020-01-02", "## Errands\nPicked up the bikes.\n")
    script(tmp_path, dreaming=DISMISSING)

    # The wiki has never been dreamed, so the first night dreams, after the night's run.
    night(data_root, vault, FIRST_NIGHT)
    phases = [session["phase"] for session in all_sessions(tmp_path)]
    assert phases[-3:] == ["observation", "consolidation", "dreaming"]
    assert dreamed_at(data_root) == FIRST_NIGHT

    # Six days on it isn't due, however late that night starts.
    outcome = night(data_root, vault, FIRST_NIGHT + timedelta(days=6, hours=20))
    assert outcome.dreaming is None and not outcome.failed
    assert dreamed_at(data_root) == FIRST_NIGHT

    # A week on it is, though the night starts a little earlier than the last one did.
    a_week_on = FIRST_NIGHT + timedelta(days=7) - timedelta(minutes=20)
    outcome = night(data_root, vault, a_week_on)
    assert outcome.dreaming is not None
    assert dreamed_at(data_root) == a_week_on


def test_the_interval_is_configurable_and_zero_turns_the_step_off(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None, monkeypatch: pytest.MonkeyPatch
):
    script(tmp_path, dreaming=DISMISSING)
    monkeypatch.setenv(INTERVAL, "0")

    outcome = night(data_root, vault, FIRST_NIGHT)

    assert outcome.dreaming is None and not outcome.failed
    assert sessions(tmp_path, "dreaming") == [] and dreamed_at(data_root) is None

    monkeypatch.setenv(INTERVAL, "2")
    night(data_root, vault, FIRST_NIGHT + timedelta(days=1))
    night(data_root, vault, FIRST_NIGHT + timedelta(days=2))
    assert dreamed_at(data_root) == FIRST_NIGHT + timedelta(days=1)
    night(data_root, vault, FIRST_NIGHT + timedelta(days=3))
    assert dreamed_at(data_root) == FIRST_NIGHT + timedelta(days=3)


def test_a_night_whose_librarian_run_failed_doesnt_dream(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None
):
    write_note(vault, "2020-01-02", "## Errands\nPicked up the bikes.\n")
    script(tmp_path, observation=[{"fail": "API Error: overloaded"}], dreaming=DISMISSING)

    outcome = night(data_root, vault, FIRST_NIGHT)

    assert outcome.librarian_failure is not None and outcome.dreaming is None
    assert outcome.dreaming_failure is None
    assert sessions(tmp_path, "dreaming") == [] and dreamed_at(data_root) is None
    assert outcome.snapshot is not None

    # It was only that night: the next one, whose run succeeds, dreams.
    script(tmp_path, dreaming=DISMISSING)
    night(data_root, vault, FIRST_NIGHT + timedelta(days=1))
    assert dreamed_at(data_root) == FIRST_NIGHT + timedelta(days=1)


def test_a_night_whose_librarian_run_was_held_back_doesnt_dream(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None
):
    write_note(vault, "2020-01-02", "## Errands\nPicked up the bikes.\n")
    script(tmp_path, dreaming=DISMISSING)
    catalog = data_root / "catalog" / "catalog.sqlite"
    catalog.unlink()
    catalog.mkdir()  # so the catalog can't open, and the night's cycle fails to ingest

    outcome = night(data_root, vault, FIRST_NIGHT)

    assert outcome.librarian_failure == HELD_BACK and outcome.dreaming is None
    assert sessions(tmp_path, "dreaming") == [] and dreamed_at(data_root) is None
    assert outcome.snapshot is not None


def test_a_failed_dreaming_run_is_recorded_and_the_backup_still_runs(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None, capsys: pytest.CaptureFixture[str]
):
    script(tmp_path, dreaming=[{"fail": "API Error: overloaded while reading [[kitchen]]"}])
    committed = wiki_head(data_root)
    capsys.readouterr()

    assert main(["nightly"]) == 1

    wiki = status(data_root).wiki
    assert wiki.last_run is not None and wiki.last_run.succeeded
    assert wiki.last_dreaming is not None and not wiki.last_dreaming.succeeded
    reason = "the run failed in its dreaming session and was discarded"
    assert wiki.last_dreaming.reason == reason
    # The backup ran, over the wiki as the night's librarian run left it.
    assert backups(tmp_path)[-1]["wiki_head"] == wiki_head(data_root) == committed
    assert status(data_root).backup.last_success is not None
    output = capsys.readouterr()
    assert f"pa: nightly: dreaming: {reason}" in output.err
    assert "nightly: backup: took snapshot 5d3f0a9b" in output.out
    recorded = (data_root / "status/status.json").read_text()
    for value in ("kitchen", "Kitchen", "overloaded", "Swim"):
        assert value not in output.out + output.err + recorded, value


def test_a_failed_dreaming_run_is_tried_again_the_next_night(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None
):
    script(tmp_path, dreaming=[{"fail": "API Error: overloaded"}])
    outcome = night(data_root, vault, FIRST_NIGHT)
    assert outcome.dreaming_failure is not None and outcome.snapshot is not None

    script(tmp_path, dreaming=DISMISSING)
    outcome = night(data_root, vault, FIRST_NIGHT + timedelta(days=1))

    assert outcome.dreaming is not None and outcome.dreaming.commit is not None
    assert len(sessions(tmp_path, "dreaming")) == 2
    run = status(data_root).wiki.last_dreaming
    assert run is not None and run.succeeded and run.at == FIRST_NIGHT + timedelta(days=1)


def test_a_night_with_nothing_to_judge_is_recorded_and_counts_as_that_weeks_review(
    data_root: Path, vault: Path, tmp_path: Path, orphan: None
):
    script(tmp_path, dreaming=DISMISSING)
    night(data_root, vault, FIRST_NIGHT)
    dreaming_commit = wiki_head(data_root)

    # A week on, the one candidate is held back: no session and no commit, but a review.
    a_week_on = FIRST_NIGHT + timedelta(days=7)
    outcome = night(data_root, vault, a_week_on)
    assert outcome.dreaming is not None and outcome.dreaming.commit is None
    run = status(data_root).wiki.last_dreaming
    assert run is not None
    assert (run.at, run.succeeded, run.candidates, run.held_back) == (a_week_on, True, 0, 1)
    assert wiki_head(data_root) == dreaming_commit

    # So the next night doesn't weigh the same candidates again, though the last dreaming
    # commit is 8 days old; the night a week after the review does.
    assert night(data_root, vault, a_week_on + timedelta(days=1)).dreaming is None
    assert dreamed_at(data_root) == a_week_on
    assert night(data_root, vault, a_week_on + timedelta(days=7)).dreaming is not None
    assert len(sessions(tmp_path, "dreaming")) == 1


def test_a_reverted_dreaming_run_is_due_again_the_next_night(
    data_root: Path, vault: Path, tmp_path: Path, orphan: str
):
    on_hold = kitchen_page(orphan).replace("remodel.", "remodel, on hold.")
    fixing = [
        {"write": "topics/kitchen.md", "text": on_hold},
        {"outcomes": [{"candidate": "c01", "outcome": "applied", "pages": ["topics/kitchen.md"]}]},
    ]
    script(tmp_path, dreaming=fixing)
    night(data_root, vault, FIRST_NIGHT)
    assert head_message(data_root).startswith("dreaming run: 1 candidates, 1 applied")
    subprocess.run(
        [
            "git",
            f"--git-dir={data_root / 'wiki.git'}",
            f"--work-tree={data_root / 'wiki'}",
            "-c",
            "user.name=Argus McNevans",
            "-c",
            "user.email=argus@example.com",
            "revert",
            "--no-edit",
            "HEAD",
        ],
        capture_output=True,
        check=True,
    )

    outcome = night(data_root, vault, FIRST_NIGHT + timedelta(days=1))

    # The run no longer stands, so the wiki's last review is none: its candidate is judged
    # again.
    assert outcome.dreaming is not None and outcome.dreaming.commit is not None
    assert len(sessions(tmp_path, "dreaming")) == 2
