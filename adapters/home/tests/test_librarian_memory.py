"""Dreaming's memory (#146): what a run offers after earlier runs judged the wiki, through the
stand-in `claude` (the conftest at the repository root says what it does and how it's scripted).

The memory is the wiki's git history: each dreaming commit lists its candidates with their
outcomes. These tests run `pa librarian dream` and `pa librarian run` over a temporary data
root, change the wiki and its history between runs (a nightly run, a commit by hand, a real
`git revert`), and assert on what the next session is offered.
"""

import json
import subprocess
import textwrap
from datetime import UTC, datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.librarian.candidates import ORDER, CandidateKind
from pa_core.librarian.dreaming import DreamingOutcome, last_dreaming_at
from pa_home import librarian
from pa_home.cli import main
from pa_home.config import DataRoot, load_librarian_config, load_owner, load_time_zone
from pa_home.cut import take_cut
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


def sessions(data_root: Path, phase: str | None = None) -> list[dict]:
    """What each session was given, in order; only `phase`'s sessions if given."""
    log = data_root.parent / "sessions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [r for r in records if phase is None or r["phase"] == phase]


def run_librarian() -> int:
    return main(["librarian", "run"])


def dream() -> int:
    return main(["librarian", "dream"])


def git(data_root: Path, *args: str) -> str:
    """Git on the wiki, as the owner would run it by hand."""
    return subprocess.run(
        [
            "git",
            f"--git-dir={data_root / 'wiki.git'}",
            f"--work-tree={data_root / 'wiki'}",
            "-c",
            "user.name=Argus McNevans",
            "-c",
            "user.email=argus@example.com",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def commit_messages(data_root: Path) -> list[str]:
    """Every commit's message, newest first."""
    return [m.strip() for m in git(data_root, "log", "--format=%B%x00").split("\0") if m.strip()]


def fields(message: str) -> dict[str, str]:
    """A commit message's `Field: value` lines: its last paragraph."""
    return dict(line.split(": ", 1) for line in message.strip().split("\n\n")[-1].splitlines())


def wiki_files(data_root: Path) -> dict[str, str]:
    wiki = data_root / "wiki"
    return {
        p.relative_to(wiki).as_posix(): p.read_text()
        for p in sorted(wiki.rglob("*"))
        if p.is_file()
    }


PERSON, TOPIC, KITCHEN = "people/david-kim.md", "topics/wedding.md", "topics/kitchen.md"


def loop(what: str = "the room list", opened: int = 1) -> str:
    """An open loop on the wedding's page, opened `opened` days ago."""
    return f"- [ ] [[david-kim]] → me: {what} · asked {days_ago(opened)} [ep:{HOTEL}]"


def pages(*loops: str) -> dict[str, str]:
    """David Kim's page, his wedding's with `loops`, and a page about the kitchen that nothing
    links to: an orphan, the wiki's one candidate unless a loop is old enough to be another."""
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
    """One nightly run over the hotel email and a later one sending the room list, whose
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


def dismissing(count: int = 1) -> list[dict]:
    """What a dreaming session does to dismiss its `count` candidates."""
    return [
        {
            "outcomes": [
                {"candidate": f"c{number:02d}", "outcome": "dismissed"}
                for number in range(1, count + 1)
            ]
        }
    ]


def offered(data_root: Path) -> list[list[str]]:
    """What each dreaming session so far was offered: every candidate as its id, its kind, and
    what it's about, as the dreaming commit lists it."""
    return [
        [
            " · ".join(
                [
                    candidate["id"],
                    candidate["kind"],
                    ", ".join(candidate["pages"])
                    + "".join(f" [ep:{episode}]" for episode in candidate.get("episodes", ())),
                ]
            )
            for candidate in session["candidates"]
        ]
        for session in sessions(data_root, "dreaming")
    ]


ORPHAN = f"c01 · orphan · {KITCHEN}"
"""The kitchen's page as a run's only candidate."""


def test_a_dismissed_candidate_isnt_offered_by_the_next_run(data_root: Path, capsys):
    run_with_pages(data_root)
    script(data_root, dreaming=dismissing())
    assert dream() == 0
    history = commit_messages(data_root)
    capsys.readouterr()

    assert dream() == 0

    # The one candidate was dismissed, so the second run had nothing to judge: no session and
    # no commit.
    assert offered(data_root) == [[ORPHAN]]
    assert commit_messages(data_root) == history
    said = "librarian dream: nothing to judge; every candidate is held back (1)"
    assert said in capsys.readouterr().out


def night(data_root: Path, subject: str, written: dict[str, str]) -> None:
    """A later nightly run over one new email about `subject`, whose consolidation writes
    `written`."""
    ingest(
        data_root,
        {
            f"gmail/{subject.lower()}.eml": email(
                message_id=f"{subject.lower()}@example.net", subject=subject
            )
        },
    )
    script(
        data_root,
        observation=[{"observe": True, "links": {subject: ["david-kim"]}}],
        consolidation=[{"write": path, "text": text} for path, text in written.items()],
    )
    assert run_librarian() == 0


def test_a_dismissed_candidate_is_offered_again_once_one_of_its_pages_changes(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=dismissing())
    assert dream() == 0

    # A night that changes another page leaves it dismissed.
    night(data_root, "Gift", {TOPIC: pages()[TOPIC].replace("on Oct 24.", "on Oct 24, in Austin.")})
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN]]

    # A night that changes its page brings it back.
    night(data_root, "Quote", {KITCHEN: pages()[KITCHEN].replace("remodel.", "remodel, on hold.")})
    script(data_root, dreaming=dismissing())
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN], [ORPHAN]]


def dream_at(data_root: Path, moment: datetime) -> DreamingOutcome:
    """A dreaming run told that `moment` is now, as the nightly job would start one then."""
    return librarian.dream(
        DataRoot(data_root),
        owner=load_owner(),
        time_zone=load_time_zone(),
        config=load_librarian_config(),
        now=lambda: moment,
    )


def test_a_dismissed_candidate_is_offered_again_after_thirty_days(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=dismissing())
    dismissed = datetime.now(CHICAGO)
    dream_at(data_root, dismissed)

    # Its page is as it was, so for 30 days it stays dismissed.
    assert dream_at(data_root, dismissed + timedelta(days=29)).candidates == ()
    assert offered(data_root) == [[ORPHAN]]

    dream_at(data_root, dismissed + timedelta(days=30))
    assert offered(data_root) == [[ORPHAN], [ORPHAN]]


PROPOSALS = "proposals.md"
ASKED = (
    f"- [ ] P1 · question · Is [[kitchen]] still a project of yours? "
    f"· proposed {days_ago(0)} [ep:{HOTEL}]"
)
"""The proposal a session asks about the kitchen's page."""
CHAT = Path(__file__).parents[3] / "core/tests/fixtures/assistant_chat/single_exchange.json"


def proposals_file(*opened: str, answered: tuple[str, ...] = ()) -> str:
    return "\n".join(["# Proposals", "", "## Open", *opened, "", "## Answered", *answered, ""])


def commit_by_hand(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, as a nightly run that
    applied the owner's answer would have left them."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


def test_a_candidate_isnt_offered_while_a_proposal_about_it_is_open(data_root: Path):
    run_with_pages(data_root)
    proposing = [
        {"write": PROPOSALS, "text": proposals_file(ASKED)},
        {"outcomes": [{"candidate": "c01", "outcome": "proposed", "proposal": "P1"}]},
    ]
    script(data_root, dreaming=proposing)
    assert dream() == 0

    # The owner hasn't answered, so the next run doesn't judge the page again.
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN]]

    # The owner answers in chat, and a nightly run marks the proposal answered. Nothing links
    # the page yet, so it is a candidate again.
    ingest(data_root, {"assistant_chat/answer.json": CHAT.read_bytes()})
    script(data_root)
    assert run_librarian() == 0
    [answer] = sessions(data_root, "observation")[-1]["episodes"]
    answered = ASKED.replace("[ ]", "[x]").replace(
        f" [ep:{HOTEL}]", f" · answered {days_ago(0)} [ep:{HOTEL}] [ep:{answer['id']}]"
    )
    commit_by_hand(data_root, {PROPOSALS: proposals_file(answered=(answered,))})
    script(data_root, dreaming=dismissing())
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN], [ORPHAN]]


ROOMS = (
    *("attic", "basement", "bathroom", "bedroom", "chimney", "closet", "cooling", "deck"),
    *("doors", "driveway", "fence", "floors", "garage", "garden", "gutters", "hallway"),
    *("heating", "laundry", "mailbox", "nursery", "office", "pantry", "plumbing", "porch"),
    *("roof", "shed", "siding", "stairs", "windows", "wiring"),
)
"""Thirty names for pages, none nearly another's, which lint would flag as a duplicate."""


def projects() -> dict[str, str]:
    """A page for each of `ROOMS`, none linked from anywhere: thirty more orphans."""
    return {
        f"topics/{room}.md": pages()[KITCHEN].replace("# Kitchen", f"# {room.capitalize()}")
        for room in ROOMS
    }


def test_a_run_offers_at_most_thirty_candidates_and_the_next_run_offers_the_rest(data_root: Path):
    run_with_pages(data_root, {**pages(), **projects()})
    script(data_root, dreaming=dismissing(30))
    assert dream() == 0
    script(data_root, dreaming=dismissing(1))
    assert dream() == 0

    # 31 orphans, in the order of their paths: the last waits for the second run.
    orphans = sorted([*ROOMS, "kitchen"])
    first, rest = offered(data_root)
    assert first == [
        f"c{number:02d} · orphan · topics/{room}.md"
        for number, room in enumerate(orphans[:30], start=1)
    ]
    assert rest == ["c01 · orphan · topics/wiring.md"]
    summary = commit_messages(data_root)[1].splitlines()[0]
    assert summary == "dreaming run: 30 candidates, 0 applied, 0 proposed, 30 dismissed"


def mismatched() -> dict[str, str]:
    """The pages with the wedding settled while a loop of its has been open 40 days: a status
    that disagrees with its loops, an orphan, and an aging loop, as the kinds are ordered."""
    written = pages(loop(opened=40))
    return {**written, TOPIC: written[TOPIC].replace("status: active", "status: settled")}


MISMATCH = f"c01 · status-mismatch · {TOPIC}"
AGING = f"loop-aging · {TOPIC} [ep:{HOTEL}]"


def test_the_cap_follows_the_configuration_and_cuts_the_fixed_order_of_kinds(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_CANDIDATE_CAP", "2")
    run_with_pages(data_root, mismatched())
    script(data_root, dreaming=dismissing(2))
    assert dream() == 0
    script(data_root, dreaming=dismissing(1))
    assert dream() == 0

    # The kinds come in their order, whatever the pages' names; the last waits a run.
    assert offered(data_root) == [[MISMATCH, f"c02 · orphan · {KITCHEN}"], [f"c01 · {AGING}"]]


def test_without_a_cap_configured_every_kind_fits_in_one_run(data_root: Path):
    run_with_pages(data_root, mismatched())
    script(data_root, dreaming=dismissing(3))

    assert dream() == 0

    assert offered(data_root) == [[MISMATCH, f"c02 · orphan · {KITCHEN}", f"c03 · {AGING}"]]


# Reverting a run's commit. The wiki here has two candidates: the kitchen's page, an orphan,
# and the wedding's loop, open 40 days. The dreaming session links the kitchen from David Kim's
# page and dismisses the loop, so the wedding's page is the same before and after a revert.
LOOP = f"c02 · {AGING}"
LINKED = "Getting married on Oct 24, and redoing his [[kitchen]]"


def judging() -> list[dict]:
    """What the dreaming session does: fix the orphan, dismiss the loop."""
    return [
        {"write": PERSON, "text": pages()[PERSON].replace("Getting married on Oct 24", LINKED)},
        {
            "outcomes": [
                {"candidate": "c01", "outcome": "applied", "pages": [PERSON]},
                {"candidate": "c02", "outcome": "dismissed"},
            ]
        },
    ]


def revert(data_root: Path, commit: str = "HEAD") -> None:
    """The owner's plain `git revert` of one of the wiki's commits."""
    git(data_root, "revert", "--no-edit", commit)


def test_after_a_dreaming_commit_is_reverted_its_candidates_are_offered_again(data_root: Path):
    run_with_pages(data_root, pages(loop(opened=40)))
    last_run = fields(commit_messages(data_root)[0])
    script(data_root, dreaming=judging())
    assert dream() == 0
    # The fix stands and the dismissal is remembered: nothing is left to judge.
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN, LOOP]]

    revert(data_root)
    assert dream() == 0

    # The page is an orphan again, and the dismissal went with the commit that recorded it.
    assert offered(data_root) == [[ORPHAN, LOOP], [ORPHAN, LOOP]]
    # The new dreaming commit carries the cursor and version of the last run that stands.
    dreamed = fields(commit_messages(data_root)[0])
    assert dreamed["Cursor"] == last_run["Cursor"]
    assert dreamed["Librarian-Version"] == last_run["Librarian-Version"]


def cut_wiki(data_root: Path, moment: datetime) -> tuple[str | None, str | None]:
    """The librarian version and the cursor a cut taken at `moment` records for the wiki."""
    wiki = take_cut(DataRoot(data_root), now=moment).manifest.wiki
    assert wiki is not None
    assert wiki.commit == git(data_root, "rev-parse", "HEAD").strip()
    return wiki.librarian_version, wiki.cursor


def new_episodes(data_root: Path) -> list[str]:
    """The subjects of the episodes the last observation session was offered."""
    return [episode["subject"] for episode in sessions(data_root, "observation")[-1]["episodes"]]


def test_a_reverted_dreaming_commit_leaves_the_cursor_and_version_of_the_last_nightly_run(
    data_root: Path,
):
    run_with_pages(data_root, pages(loop(opened=40)))
    last_run = fields(commit_messages(data_root)[0])
    script(data_root, dreaming=judging())
    assert dream() == 0

    revert(data_root)

    # The wiki's last commit is the revert, which records nothing; a cut still names the
    # version that built the wiki and where it stopped.
    assert "Cursor" not in commit_messages(data_root)[0]
    assert cut_wiki(data_root, datetime.now(UTC)) == (
        last_run["Librarian-Version"],
        last_run["Cursor"],
    )
    # The next nightly run offers only what was captured since the last one.
    night(data_root, "Gift", {})
    assert new_episodes(data_root) == ["Gift"]


IN_AUSTIN = pages()[TOPIC].replace("on Oct 24.", "on Oct 24, in Austin.")
"""The wedding's page as a later night leaves it."""


def test_after_a_nightly_runs_commit_is_reverted_that_nights_episodes_are_offered_again(
    data_root: Path,
):
    run_with_pages(data_root)
    first_night = fields(commit_messages(data_root)[0])
    night(data_root, "Gift", {TOPIC: IN_AUSTIN})

    revert(data_root)

    # The wiki is back where the first night left it: its pages, its cursor, and its version.
    assert wiki_files(data_root)[TOPIC] == pages()[TOPIC]
    assert cut_wiki(data_root, datetime.now(UTC)) == (
        first_night["Librarian-Version"],
        first_night["Cursor"],
    )
    # So the next run takes the reverted night's episode again, with what came after it, and
    # nothing from before.
    night(data_root, "Menu", {})
    assert sorted(new_episodes(data_root)) == ["Gift", "Menu"]


def test_reverting_a_nightly_commit_under_a_dreaming_commit_offers_its_episodes_again(
    data_root: Path,
):
    run_with_pages(data_root, pages(loop(opened=40)))
    first_night = fields(commit_messages(data_root)[0])
    night(data_root, "Gift", {})
    # That night's dreaming run sits on its librarian run, and carries its cursor forward.
    script(data_root, dreaming=dismissing(2))
    assert dream() == 0
    dreamed = fields(commit_messages(data_root)[0])
    assert "Dreaming-At" in dreamed
    assert dreamed["Cursor"] != first_night["Cursor"]

    revert(data_root, "HEAD~1")

    # The cursor is the newest librarian run's that stands, never one a dreaming commit
    # carried forward from a run since reverted.
    assert cut_wiki(data_root, datetime.now(UTC)) == (
        first_night["Librarian-Version"],
        first_night["Cursor"],
    )
    night(data_root, "Menu", {})
    assert sorted(new_episodes(data_root)) == ["Gift", "Menu"]


def test_reverting_a_revert_puts_the_run_back_with_what_it_recorded(data_root: Path, capsys):
    run_with_pages(data_root, pages(loop(opened=40)))
    night(data_root, "Gift", {TOPIC: pages(loop(opened=40))[TOPIC].replace("Oct 24.", "Oct 25.")})
    gift_night = fields(commit_messages(data_root)[0])
    script(data_root, dreaming=judging())
    assert dream() == 0
    # Both runs are reverted, the newest first, and then both reverts, the newest first.
    revert(data_root)
    revert(data_root, "HEAD~2")
    revert(data_root)
    revert(data_root, "HEAD~2")
    history = commit_messages(data_root)
    capsys.readouterr()

    # The night's run stands again, so nothing is new to it.
    assert cut_wiki(data_root, datetime.now(UTC))[1] == gift_night["Cursor"]
    script(data_root)
    assert run_librarian() == 0
    assert "no new episodes" in capsys.readouterr().out
    # The dreaming run stands again: its fix is back, and its dismissal is remembered.
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN, LOOP]]
    assert commit_messages(data_root) == history


def test_reverting_an_older_dreaming_commit_forgets_it_and_leaves_the_later_runs(data_root: Path):
    run_with_pages(data_root, pages(loop(opened=40)))
    script(data_root, dreaming=judging())
    assert dream() == 0
    night(data_root, "Gift", {})
    gift_night = fields(commit_messages(data_root)[0])

    revert(data_root, "HEAD~1")

    # The night after it stands, so the wiki is still where that night left it.
    assert cut_wiki(data_root, datetime.now(UTC)) == (
        gift_night["Librarian-Version"],
        gift_night["Cursor"],
    )
    # What the dreaming run fixed and dismissed is judged again.
    script(data_root, dreaming=judging())
    assert dream() == 0
    assert offered(data_root) == [[ORPHAN, LOOP], [ORPHAN, LOOP]]
    assert fields(commit_messages(data_root)[0])["Cursor"] == gift_night["Cursor"]


def test_a_dreaming_commit_that_changed_no_file_is_reverted_by_concluding_gits_empty_revert(
    data_root: Path,
):
    run_with_pages(data_root)
    script(data_root, dreaming=dismissing())
    assert dream() == 0

    # Git has nothing to undo, so it stops before committing; the owner concludes the revert
    # as an empty commit, which carries git's own message.
    with pytest.raises(subprocess.CalledProcessError):
        revert(data_root)
    git(data_root, "commit", "--allow-empty", "--no-edit")
    assert dream() == 0

    assert offered(data_root) == [[ORPHAN], [ORPHAN]]


def test_a_commit_by_hand_that_records_no_run_leaves_the_cursor_where_the_last_run_put_it(
    data_root: Path,
):
    run_with_pages(data_root)
    fixed = pages()[KITCHEN].replace("remodel.", "remodel, on hold.")
    (data_root / "wiki" / KITCHEN).write_text(fixed)
    git(data_root, "add", "--all")
    git(data_root, "commit", "--message", "Fix the kitchen's summary")

    night(data_root, "Gift", {})

    assert new_episodes(data_root) == ["Gift"]
    assert wiki_files(data_root)[KITCHEN] == fixed


def test_a_wiki_whose_history_records_no_run_is_dreamed_and_remembered_like_any_other(
    data_root: Path,
):
    # A copy of an eval wiki, committed as it came: its history holds no run at all.
    ingest(data_root, {"gmail/hotel.eml": email()})
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in pages().items():
        (data_root / "wiki" / path).parent.mkdir(parents=True, exist_ok=True)
        (data_root / "wiki" / path).write_text(text)
    repository.commit("Copy the eval wiki\n")
    script(data_root, dreaming=dismissing(4))

    assert dream() == 0
    assert dream() == 0

    # No observation links the pages, so each is an orphan, and the person's is unengaged
    # too; the second run remembers the first.
    assert offered(data_root) == [
        [
            f"c01 · orphan · {PERSON}",
            f"c02 · orphan · {KITCHEN}",
            f"c03 · orphan · {TOPIC}",
            f"c04 · unengaged · {PERSON}",
        ]
    ]
    # With no run before it, the dreaming commit has no cursor or version to carry.
    recorded = fields(commit_messages(data_root)[0])
    assert "Dreaming-At" in recorded
    assert "Cursor" not in recorded
    assert "Librarian-Version" not in recorded


def test_every_kind_of_candidate_has_its_place_in_the_order():
    assert sorted(kind for row in ORDER for kind in row) == sorted(CandidateKind)


def test_the_last_dreaming_run_that_stands_is_read_from_the_wikis_history(data_root: Path):
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    run_with_pages(data_root, pages(loop(opened=40)))
    # A wiki that was never dreamed has none.
    assert last_dreaming_at(repository) is None

    first = datetime.now(CHICAGO).replace(microsecond=0)
    script(data_root, dreaming=judging())
    dream_at(data_root, first)
    assert last_dreaming_at(repository) == first

    # A nightly run after it changes nothing, and neither does a dreaming run with nothing to
    # judge, which commits nothing.
    night(data_root, "Gift", {})
    assert dream_at(data_root, first + timedelta(days=7)).commit is None
    assert last_dreaming_at(repository) == first
    # A later dreaming run, judging the loop again and closing it, takes its place.
    asked = loop(opened=40)
    closed = asked.replace("- [ ]", "- [x]") + f" · closed {days_ago(0)} [ep:{HOTEL}]"
    script(
        data_root,
        dreaming=[
            {"write": TOPIC, "text": pages(closed)[TOPIC]},
            {"outcomes": [{"candidate": "c01", "outcome": "applied", "pages": [TOPIC]}]},
        ],
    )
    dream_at(data_root, first + timedelta(days=35))
    assert last_dreaming_at(repository) == first + timedelta(days=35)

    # Reverted, it no longer stands, and the one before it is the last again.
    revert(data_root)
    assert last_dreaming_at(repository) == first
