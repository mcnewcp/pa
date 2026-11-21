"""What a dreaming run leaves in the status (#148), through the stand-in `claude` (the conftest
at the repository root says what it does and how it's scripted).

These tests run `pa librarian dream` and `pa librarian run` over a temporary data root and
assert on the status file and on what the commands print: the dreaming record after a success,
a failure, and a run with nothing to judge, and that neither kind of run erases the other's
record. The status holds counts and reasons only.
"""

import json
import subprocess
import textwrap
from datetime import datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.status import DreamingRun, Status, read_status
from pa_home.cli import main
from pa_home.git_wiki import GitWikiRepository

CHICAGO = ZoneInfo("America/Chicago")
"""The owner's time zone."""


def days_ago(days: int) -> str:
    """The date `days` days before today in the owner's time zone; runs are told the clock."""
    return (datetime.now(CHICAGO).date() - timedelta(days=days)).isoformat()


def mailed(days: int, clock: str) -> str:
    """An email's `Date`: `clock` in the owner's time zone, `days` days before today."""
    moment = datetime.combine(
        datetime.now(CHICAGO).date() - timedelta(days=days), time.fromisoformat(clock), CHICAGO
    )
    return format_datetime(moment)


# The wiki's mail is dated by the clock, as the runs are: a page's age is measured from today.
ROOM_LIST_DAY = days_ago(10)
HOTEL = "gmail_9ff7394d0df8584d"
"""The id of the episode `email()` makes by default."""


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    date: str = mailed(12, "21:30"),
    sender: str = "David Kim <dkim@example.net>",
    to: str = "Argus McNevans <argus@example.com>",
    subject: str = "Hotel block",
    body: str = "The hotel block closes on Oct 9.",
) -> bytes:
    return (
        f"X-Gmail-Labels: {labels}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        f"Date: {date}\r\n"
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode()


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stand_in_claude: Path) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", "Gus")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_LIBRARIAN_COMMAND", str(stand_in_claude))
    for var in (
        "PA_LIBRARIAN_MODEL",
        "PA_LIBRARIAN_EFFORT",
        "PA_LIBRARIAN_MAX_TURNS",
        "PA_LIBRARIAN_TIMEOUT_SECONDS",
        "PA_LIBRARIAN_PAGE_THRESHOLD",
        "PA_LIBRARIAN_LOOP_AGE_DAYS",
        "PA_LIBRARIAN_STALE_LATER_DAYS",
        "PA_LIBRARIAN_QUIET_DAYS",
        "PA_LIBRARIAN_NEW_PROPOSALS",
        "PA_LIBRARIAN_OPEN_PROPOSALS",
        "PA_LIBRARIAN_CANDIDATE_CAP",
        "PA_LIBRARIAN_DREAMING_INTERVAL_DAYS",
        "PA_LIBRARIAN_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    return root


def ingest(data_root: Path, payloads: dict[str, bytes]) -> None:
    """Drop each payload at its path in the inbox (such as `gmail/hotel.eml`), then ingest."""
    for name, payload in payloads.items():
        target = data_root / "inbox" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    assert main(["ingest"]) == 0


def script(data_root: Path, **phases: list[dict]) -> None:
    (data_root.parent / "script.json").write_text(json.dumps(phases))


def sessions(data_root: Path, phase: str) -> list[dict]:
    """What each of `phase`'s sessions was given, in order."""
    log = data_root.parent / "sessions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [record for record in records if record["phase"] == phase]


def commit_messages(data_root: Path) -> list[str]:
    """Every commit's message, newest first."""
    logged = subprocess.run(
        ["git", f"--git-dir={data_root / 'wiki.git'}", "log", "--format=%B%x00"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [message.strip() for message in logged.split("\0") if message.strip()]


def run_librarian() -> int:
    return main(["librarian", "run"])


def dream() -> int:
    return main(["librarian", "dream"])


def status(data_root: Path) -> Status:
    return read_status(data_root / "status" / "status.json")


def dreaming(data_root: Path) -> DreamingRun:
    """The status's record of the last dreaming run."""
    run = status(data_root).wiki.last_dreaming
    assert run is not None
    return run


PERSON, TOPIC, KITCHEN = "people/david-kim.md", "topics/wedding.md", "topics/kitchen.md"
PROPOSALS = "proposals.md"


def loop(what: str = "the room list", opened: int = 40) -> str:
    """An open loop on the wedding's page, opened `opened` days ago: old enough to be aging."""
    return f"- [ ] [[david-kim]] → me: {what} · asked {days_ago(opened)} [ep:{HOTEL}]"


def pages(*loops: str) -> dict[str, str]:
    """David Kim's page, his wedding's with `loops`, and a page about the kitchen that nothing
    links to: an orphan, which is candidate `c01`. An aging loop is `c02`."""
    return {
        PERSON: textwrap.dedent(
            f"""\
            ---
            type: person
            aliases: [Dave]
            status: active
            summary: Old friend, getting married on Oct 24.
            updated: {ROOM_LIST_DAY}
            emails: [dkim@example.net]
            ---
            # David Kim

            ## Current state
            Getting married on Oct 24 [ep:{HOTEL}].

            ## Open loops

            ## Notes
            """
        ),
        TOPIC: textwrap.dedent(
            f"""\
            ---
            type: topic
            aliases: [Dave's wedding]
            status: active
            summary: Dave's wedding on Oct 24.
            updated: {ROOM_LIST_DAY}
            ---
            # Wedding

            ## Current state
            The hotel block closes on Oct 9 [ep:{HOTEL}].

            ## Decisions

            ## Open loops
            """
        )
        + "\n".join(loops)
        + "\n\n## Notes\n",
        KITCHEN: textwrap.dedent(
            f"""\
            ---
            type: topic
            aliases: []
            status: active
            summary: The kitchen remodel.
            updated: {ROOM_LIST_DAY}
            ---
            # Kitchen

            ## Current state
            Waiting on a quote [ep:{HOTEL}].

            ## Decisions

            ## Open loops

            ## Notes
            """
        ),
    }


LINKS = {"Hotel block": ["david-kim", "wedding"], "Room list": ["david-kim", "wedding"]}
"""The slugs the stand-in's observations link, by the episode's subject."""


def run_with_pages(data_root: Path, written: dict[str, str] | None = None) -> None:
    """One librarian run over the hotel email and a later one sending the room list, whose
    consolidation writes `written` (`pages()` unless given)."""
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/room-list.eml": email(
                message_id="wedding-0002@example.net",
                labels="Sent",
                date=mailed(10, "09:00"),
                sender="Argus McNevans <argus@example.com>",
                to="David Kim <dkim@example.net>",
                subject="Room list",
                body="Here is the room list.",
            ),
        },
    )
    written = pages() if written is None else written
    script(
        data_root,
        observation=[{"observe": True, "links": LINKS}],
        consolidation=[{"write": path, "text": text} for path, text in written.items()],
    )
    assert run_librarian() == 0


def night(data_root: Path, subject: str = "Gift") -> None:
    """A later librarian run over one new email about `subject`, which changes no page."""
    ingest(
        data_root,
        {
            f"gmail/{subject.lower()}.eml": email(
                message_id=f"{subject.lower()}@example.net", subject=subject
            )
        },
    )
    script(data_root, observation=[{"observe": True, "links": {subject: ["david-kim"]}}])
    assert run_librarian() == 0


def proposal(number: int, text: str = "Is [[kitchen]] still a project of yours?") -> str:
    return f"- [ ] P{number} · question · {text} · proposed {days_ago(0)} [ep:{HOTEL}]"


def proposals_file(*opened: str) -> str:
    return "\n".join(["# Proposals", "", "## Open", *opened, "", "## Answered", ""])


DISMISSED = {"candidate": "c01", "outcome": "dismissed"}
"""The orphan, dismissed."""
JUDGING = [
    {"write": PROPOSALS, "text": proposals_file(proposal(1, "Did [[david-kim]] get the list?"))},
    {"outcomes": [DISMISSED, {"candidate": "c02", "outcome": "proposed", "proposal": "P1"}]},
]
"""What a dreaming session does to dismiss the orphan and ask the owner about the loop."""


def test_a_dreaming_run_is_recorded_in_the_status_in_counts(data_root: Path):
    run_with_pages(data_root, pages(loop()))
    nightly = status(data_root).wiki
    script(data_root, dreaming=JUDGING)
    before = datetime.now(CHICAGO)

    assert dream() == 0

    run = dreaming(data_root)
    assert before <= run.at <= datetime.now(CHICAGO)
    assert run == DreamingRun(
        at=run.at,
        succeeded=True,
        cost_usd=0.0421,
        candidates=2,
        outcomes={"applied": 0, "proposed": 1, "dismissed": 1},
        held_back=0,
        left_over=0,
    )
    wiki = status(data_root).wiki
    assert wiki.open_proposals == 1
    # The librarian run's own record stands, and the wiki is current through where it was.
    assert nightly.last_run is not None and nightly.current_through is not None
    assert (wiki.last_run, wiki.current_through) == (nightly.last_run, nightly.current_through)


def test_pa_status_shows_the_dreaming_run_and_the_open_proposals_without_content(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    run_with_pages(data_root, pages(loop()))
    script(data_root, dreaming=JUDGING)
    assert dream() == 0
    capsys.readouterr()

    assert main(["status"]) == 0

    output = capsys.readouterr()
    lines = dict(line.split(": ", 1) for line in output.out.splitlines())
    assert lines["dreaming"].endswith(
        " succeeded, cost $0.04, judged 2 candidates (0 applied, 1 proposed, 1 dismissed)"
    )
    assert lines["proposals"] == "1 open"
    recorded = (data_root / "status" / "status.json").read_text()
    for value in ("kitchen", "wedding", "david", "Hotel", "list?", HOTEL):
        assert value not in output.out + output.err + recorded, value


def test_a_librarian_run_keeps_the_dreaming_record(data_root: Path):
    run_with_pages(data_root, pages(loop()))
    script(data_root, dreaming=JUDGING)
    assert dream() == 0
    dreamed = dreaming(data_root)

    night(data_root)

    wiki = status(data_root).wiki
    assert wiki.last_dreaming == dreamed
    assert wiki.open_proposals == 1
    assert wiki.last_run is not None and wiki.last_run.at > dreamed.at


# Distinctive text that must never reach the output or the status file.
SALT = "Quillon Vasquez-Ferro booked the Bellwether loft"


def test_a_failed_dreaming_run_is_recorded_with_a_reason_that_holds_no_content(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    run_with_pages(data_root, pages(loop()))
    script(data_root, dreaming=JUDGING)
    assert dream() == 0
    # The next run breaks its bounds, and reports outcomes that quote what it shouldn't.
    night(data_root)
    script(
        data_root,
        dreaming=[
            {"write": "notes/quillon.md", "text": SALT},
            {"final": f"I couldn't judge [[kitchen]]: {SALT}."},
        ],
    )
    # The dismissal is held back, and the proposal holds the loop back, so plant a new orphan.
    plant(data_root, {"topics/garden.md": pages()[KITCHEN].replace("Kitchen", "Garden")})
    capsys.readouterr()

    assert dream() == 1

    run = dreaming(data_root)
    assert not run.succeeded
    assert run.reason == "the run failed in its dreaming session and was discarded"
    assert (run.cost_usd, run.candidates, run.outcomes, run.held_back) == (0.0421, None, {}, None)
    # What is open is what the last commit holds: the failed run's wiki was discarded.
    assert status(data_root).wiki.open_proposals == 1
    recorded = (data_root / "status" / "status.json").read_text()
    for value in (SALT, "Quillon", "kitchen", "garden", "wedding", "david", "Hotel", "list?"):
        assert value not in recorded, value


def plant(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, as a run would have left
    them."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


def test_a_run_with_no_candidates_is_recorded_as_a_review_that_judged_nothing(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    # A wiki with nothing flagged: the kitchen's page is linked, so it's no orphan.
    linked = pages()
    linked[TOPIC] = linked[TOPIC].replace("closes on Oct 9", "closes on Oct 9, see [[kitchen]]")
    run_with_pages(data_root, linked)
    capsys.readouterr()

    assert dream() == 0

    assert sessions(data_root, "dreaming") == []
    run = dreaming(data_root)
    assert (run.succeeded, run.candidates, run.held_back) == (True, 0, 0)
    assert run.outcomes == {"applied": 0, "proposed": 0, "dismissed": 0}
    assert "librarian dream: no candidates in the wiki" in capsys.readouterr().out


def test_a_run_whose_candidates_are_all_held_back_says_so_and_is_recorded(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    run_with_pages(data_root, pages(loop()))
    script(data_root, dreaming=JUDGING)
    assert dream() == 0
    capsys.readouterr()

    assert dream() == 0

    # The orphan was dismissed and the loop is asked about: nothing is left to judge.
    assert len(sessions(data_root, "dreaming")) == 1
    run = dreaming(data_root)
    assert (run.succeeded, run.candidates, run.held_back) == (True, 0, 2)
    assert (
        "librarian dream: nothing to judge; every candidate is held back (2)"
        in capsys.readouterr().out
    )


def bare(page_type: str, name: str, identifiers: str, loop: str = "") -> str:
    """A page of `page_type` for `name` that holds nothing but, perhaps, one `loop`."""
    return (
        textwrap.dedent(
            f"""\
        ---
        type: {page_type}
        aliases: []
        status: active
        summary: Wrote once.
        updated: {ROOM_LIST_DAY}
        {identifiers}: []
        ---
        # {name}

        ## Current state
        Wrote once [ep:{HOTEL}].

        ## Open loops
        """
        )
        + (f"{loop}\n" if loop else "")
        + "\n## Notes\n"
    )


NINA, PARCEL_POST, SAM = "people/nina-park.md", "orgs/parcel-post.md", "people/sam-lee.md"


def strangers() -> dict[str, str]:
    """The wedding's pages, and three more the owner never had a hand in: a person with no
    loop, an organization that owes something, and a person the wedding's loop names. With
    the kitchen, three orphans (Sam Lee is linked by the loop) and three unengaged pages."""
    opened = days_ago(1)
    return {
        **pages(f"- [ ] [[sam-lee]] → me: the deposit · asked {opened} [ep:{HOTEL}]"),
        NINA: bare("person", "Nina Park", "emails"),
        PARCEL_POST: bare(
            "organization",
            "Parcel Post",
            "domains",
            f"- [ ] [[parcel-post]] → me: the tracking number · promised {opened} [ep:{HOTEL}]",
        ),
        SAM: bare("person", "Sam Lee", "emails"),
    }


def counted(capsys: pytest.CaptureFixture[str]) -> list[str]:
    """What `pa librarian dream --count` prints."""
    capsys.readouterr()
    assert main(["librarian", "dream", "--count"]) == 0
    output = capsys.readouterr()
    for value in ("nina", "parcel", "sam", "kitchen", "wedding", "david", "Hotel", HOTEL):
        assert value not in output.out + output.err, value
    return output.out.splitlines()


def test_the_count_says_what_a_run_would_find_in_counts_alone_and_runs_nothing(
    data_root: Path, capsys: pytest.CaptureFixture[str]
):
    run_with_pages(data_root, strangers())
    history = commit_messages(data_root)
    recorded = (data_root / "status" / "status.json").read_text()

    assert counted(capsys) == [
        "librarian dream: 6 candidates flagged (3 orphan, 3 unengaged)",
        "librarian dream: a run now would be offered 6, with 0 held back and 0 over the cap of 30",
        # Nina Park alone: Parcel Post holds a loop, and the wedding's loop names Sam Lee.
        "librarian dream: 1 unengaged pages with no open loop (1 people, 0 organizations)",
    ]
    # No session ran, nothing was committed, and the status wasn't touched.
    assert sessions(data_root, "dreaming") == []
    assert commit_messages(data_root) == history
    assert (data_root / "status" / "status.json").read_text() == recorded


def test_a_run_cut_by_the_cap_says_how_many_candidates_it_left_over(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("PA_LIBRARIAN_CANDIDATE_CAP", "4")
    run_with_pages(data_root, strangers())
    assert counted(capsys)[1] == (
        "librarian dream: a run now would be offered 4, with 0 held back and 2 over the cap of 4"
    )
    dismissing = [{"candidate": f"c0{number}", "outcome": "dismissed"} for number in (1, 2, 3, 4)]
    script(data_root, dreaming=[{"outcomes": dismissing}])

    assert dream() == 0

    assert "judged 4 candidates (0 applied, 0 proposed, 4 dismissed)" in capsys.readouterr().out
    run = dreaming(data_root)
    assert (run.candidates, run.held_back, run.left_over) == (4, 0, 2)
    assert main(["status"]) == 0
    lines = dict(line.split(": ", 1) for line in capsys.readouterr().out.splitlines())
    assert lines["dreaming"].endswith(
        "judged 4 candidates (0 applied, 0 proposed, 4 dismissed), 2 over the cap"
    )
    # What the run dismissed is held back, and what it left over is offered next.
    assert counted(capsys)[:2] == [
        "librarian dream: 6 candidates flagged (3 orphan, 3 unengaged)",
        "librarian dream: a run now would be offered 2, with 4 held back and 0 over the cap of 4",
    ]


def test_the_count_is_of_the_data_roots_wiki_alone(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["librarian", "dream", str(tmp_path / "copy"), "--count"]) == 1

    assert "--count counts the data root's wiki" in capsys.readouterr().err


def test_the_open_proposal_count_follows_the_wiki_at_every_run(data_root: Path):
    run_with_pages(data_root)
    assert status(data_root).wiki.open_proposals == 0

    plant(data_root, {PROPOSALS: proposals_file(proposal(1), proposal(2, "Keep [[wedding]]?"))})
    night(data_root)

    assert status(data_root).wiki.open_proposals == 2
