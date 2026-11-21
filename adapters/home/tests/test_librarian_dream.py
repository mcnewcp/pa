"""`pa librarian dream`: whole dreaming runs over a temporary data root, through the stand-in
`claude` (the conftest at the repository root says what it does and how it's scripted).

Each test builds a wiki with a nightly run (`run_with_pages`), scripts the dreaming session's
edits and the outcomes its final message reports, and runs the command. The tests assert on the
wiki, its git history, and what the session was given: the stand-in records the candidates
listed under the prompt's `## Candidates` heading as `candidates`.
"""

import json
import os
import re
import subprocess
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.librarian.candidates import CandidateKind
from pa_core.librarian.dreaming import OUTCOMES, Applied, Dismissed, Outcomes, Proposed
from pa_home.cli import main
from pa_home.config import DataRoot
from pa_home.cut import take_cut
from pa_home.librarian_project import LIBRARIAN_PROJECT
from pa_home.lock import data_root_lock

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
HOTEL_DAY, ROOM_LIST_DAY = days_ago(12), days_ago(10)
"""The days the two emails of `run_with_pages` are logged on."""
HOTEL = "gmail_9ff7394d0df8584d"
"""The id of the episode `email()` makes by default."""


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    # Sent late in Chicago, which is already the next day in UTC.
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


def run_error(data_root: Path) -> str:
    """The whole error of the last run that failed, librarian or dreaming, as the command
    keeps it under the data root: it prints only where the run failed."""
    kept = sorted((data_root / "run-errors").glob("*.txt"), key=lambda file: file.stat().st_mtime)
    return kept[-1].read_text()


def git(data_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", f"--git-dir={data_root / 'wiki.git'}", *args],
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


def tree_state(directory: Path) -> dict[str, object]:
    """Everything under `directory`, without following links: files, links, and directories."""
    state: dict[str, object] = {}
    for parent, dirs, files in os.walk(directory):
        for name in dirs + files:
            path = Path(parent) / name
            key = path.relative_to(directory).as_posix()
            if path.is_symlink():
                state[key] = ("link", str(path.readlink()))
            elif path.is_dir():
                state[key] = ("dir",)
            else:
                state[key] = ("file", path.read_bytes())
    return state


PERSON, TOPIC = "people/david-kim.md", "topics/wedding.md"
ROOM_LIST = f"- [ ] [[david-kim]] → me: the room list · asked {days_ago(40)} [ep:{HOTEL}]"
"""The wedding's loop, open 40 days: more than lint's 30."""


def wedding_pages() -> dict[str, str]:
    """Dave's page and his wedding's, each linked from the other or the day logs, with nothing
    for lint to warn about but the wedding's aging loop."""
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
            {ROOM_LIST}

            ## Notes
            """
        ),
    }


LINKS = {"Hotel block": ["david-kim", "wedding"], "Room list": ["david-kim", "wedding"]}
"""The slugs the stand-in's observations link, by the episode's subject."""


def linked(day: str, *slugs: str) -> dict:
    """The stand-in's action logging an observation on `day` that links `slugs`."""
    links = " and ".join(f"[[{slug}]]" for slug in slugs)
    return {"append": f"days/{day}.md", "text": f"- a note about {links} [ep:{HOTEL}]\n"}


def run_with_pages(
    data_root: Path, pages: dict[str, str] | None = None, logged: Sequence[dict] = ()
) -> str:
    """One nightly run over the hotel email and a later one sending the room list, whose
    observation also logs `logged` (`linked` actions) and whose consolidation writes `pages`
    (the wedding pages unless given). Returns the later email's episode id."""
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
    pages = wedding_pages() if pages is None else pages
    script(
        data_root,
        observation=[{"observe": True, "links": LINKS}, *logged],
        consolidation=[{"write": path, "text": text} for path, text in pages.items()],
    )
    assert run_librarian() == 0
    [observation] = sessions(data_root, "observation")
    [room_list] = [e["id"] for e in observation["episodes"] if e["subject"] == "Room list"]
    return room_list


def closed(room_list: str) -> str:
    """The wedding's page with its loop closed today, citing the email that sent the list."""
    return wedding_pages()[TOPIC].replace(
        ROOM_LIST,
        ROOM_LIST.replace("- [ ]", "- [x]") + f" · closed {days_ago(0)} [ep:{room_list}]",
    )


def test_a_dreaming_run_closes_a_loop_l0_closed_in_one_commit(data_root: Path):
    room_list = run_with_pages(data_root)
    history = commit_messages(data_root)
    script(
        data_root,
        dreaming=[
            {"write": TOPIC, "text": closed(room_list)},
            {"outcomes": [{"candidate": "c01", "outcome": "applied", "pages": [TOPIC]}]},
        ],
    )

    assert dream() == 0

    # The session was given the aging loop as its one candidate.
    [session] = sessions(data_root, "dreaming")
    assert session["candidates"] == [
        {
            "id": "c01",
            "kind": "loop-aging",
            "pages": [TOPIC],
            "path": TOPIC,
            "line": 16,
            "message": "a loop open 40 days, more than 30: is it settled, or still owed?",
            "episodes": [HOTEL],
        }
    ]
    # One commit, closing the loop.
    assert commit_messages(data_root)[1:] == history
    assert wiki_files(data_root)[TOPIC] == closed(room_list)
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert changed == [TOPIC]


def test_a_dreaming_commit_carries_the_cursor_and_version_forward(data_root: Path):
    run_with_pages(data_root)
    last_run = fields(commit_messages(data_root)[0])
    script(data_root, dreaming=[{"outcomes": [{"candidate": "c01", "outcome": "dismissed"}]}])

    assert dream() == 0

    dreamed = fields(commit_messages(data_root)[0])
    assert "Dreaming-At" in dreamed
    assert dreamed["Cursor"] == last_run["Cursor"]
    assert dreamed["Librarian-Version"] == last_run["Librarian-Version"]
    # A cut still reads the version, and the cursor, from the wiki's last commit.
    cut = take_cut(DataRoot(data_root), now=datetime.now(UTC))
    assert cut.manifest.wiki is not None
    assert cut.manifest.wiki.commit == git(data_root, "rev-parse", "HEAD").strip()
    assert cut.manifest.wiki.librarian_version == last_run["Librarian-Version"]
    assert cut.manifest.wiki.cursor == last_run["Cursor"]
    # The next nightly run offers only what was captured since.
    ingest(
        data_root,
        {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")},
    )
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["Gift"]


def settled_pages() -> dict[str, str]:
    """The wedding pages with the loop opened yesterday: nothing for lint to warn about."""
    pages = wedding_pages()
    pages[TOPIC] = pages[TOPIC].replace(f"asked {days_ago(40)}", f"asked {days_ago(1)}")
    return pages


def test_a_wiki_without_candidates_gets_no_session_and_no_commit(data_root: Path, capsys):
    run_with_pages(data_root, settled_pages())
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    capsys.readouterr()

    assert dream() == 0

    assert "librarian dream: no candidates in the wiki" in capsys.readouterr().out
    assert sessions(data_root, "dreaming") == []
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


KITCHEN = textwrap.dedent(
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
)
"""A topic page nothing links to."""


def test_the_commit_message_counts_the_candidates_and_outcomes_and_lists_each_one(
    data_root: Path, capsys
):
    room_list = run_with_pages(data_root, {**wedding_pages(), "topics/kitchen.md": KITCHEN})
    script(
        data_root,
        dreaming=[
            {"write": TOPIC, "text": closed(room_list)},
            {
                "outcomes": [
                    {"candidate": "c02", "outcome": "applied", "pages": [TOPIC]},
                    {"candidate": "c01", "outcome": "dismissed"},
                ]
            },
        ],
    )
    capsys.readouterr()

    assert dream() == 0

    summary, listed, last = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 2 candidates, 1 applied, 0 proposed, 1 dismissed"
    assert listed.splitlines() == [
        "Candidates:",
        "- c01 · orphan · topics/kitchen.md · dismissed",
        f"- c02 · loop-aging · {TOPIC} [ep:{HOTEL}] · applied: {TOPIC}",
    ]
    recorded = fields(last)
    assert recorded["Dreaming-Candidates"] == "2"
    assert (recorded["Dreaming-Applied"], recorded["Dreaming-Dismissed"]) == ("1", "1")
    # The run's own "now", which is the clock's here.
    told = datetime.fromisoformat(recorded["Dreaming-At"])
    assert abs(datetime.now(UTC) - told) < timedelta(minutes=1)
    assert len(recorded["Dreaming-Version"]) == 12
    assert (recorded["Model"], recorded["Effort"]) == ("claude-opus-5-5", "medium")
    assert recorded["Dreaming-Cost-USD"] == "0.0421"
    assert (recorded["Dreaming-Turns"], recorded["Dreaming-Resumes"]) == ("6", "0")
    assert recorded["Dreaming-Session"] == "session-1"
    # The kitchen is still an orphan.
    assert recorded["Lint-Warnings"] == "1"
    out = capsys.readouterr().out
    assert "judged 2 candidates (1 applied, 0 proposed, 1 dismissed)" in out
    assert "1 lint warnings" in out


def _edit(path: str, old: str, new: str):
    def edit(pages: dict[str, str]) -> None:
        assert old in pages[path], old
        pages[path] = pages[path].replace(old, new, 1)

    return edit


YESTERDAY = f"asked {days_ago(1)} [ep:"

# Each kind of lint warning: what puts it in the settled pages, the candidate the session is
# given for it (after its id, `c01`), and the environment that brings it out.
KINDS = {
    "an orphan page": (
        lambda pages: pages.update({"topics/kitchen.md": KITCHEN}),
        {
            "kind": "orphan",
            "pages": ["topics/kitchen.md"],
            "path": "topics/kitchen.md",
            "message": "an orphan page: nothing in the wiki links [[kitchen]]",
        },
        {},
    ),
    "a loop past due": (
        _edit(TOPIC, YESTERDAY, f"asked {days_ago(5)} · due {days_ago(2)} [ep:"),
        {
            "kind": "loop-past-due",
            "pages": [TOPIC],
            "path": TOPIC,
            "line": 16,
            "message": f"a loop past due: due {days_ago(2)}, 2 days ago",
            "episodes": [HOTEL],
        },
        {},
    ),
    "a loop open longer than configured": (
        _edit(TOPIC, YESTERDAY, f"asked {days_ago(11)} [ep:"),
        {
            "kind": "loop-aging",
            "pages": [TOPIC],
            "path": TOPIC,
            "line": 16,
            "message": "a loop open 11 days, more than 10: is it settled, or still owed?",
            "episodes": [HOTEL],
        },
        {"PA_LIBRARIAN_LOOP_AGE_DAYS": "10"},
    ),
    "two pages sharing an alias": (
        _edit(TOPIC, "aliases: [Dave's wedding]", "aliases: [Dave's wedding, dave]"),
        {
            "kind": "duplicate",
            "pages": [TOPIC, PERSON],
            "path": TOPIC,
            "message": f"a duplicate candidate: it shares the alias dave with {PERSON}",
        },
        {},
    ),
    "an alias that's another page's slug": (
        _edit(PERSON, "aliases: [Dave]", "aliases: [Dave, Wedding]"),
        {
            "kind": "duplicate",
            "pages": [PERSON, TOPIC],
            "path": PERSON,
            "message": f"a duplicate candidate: its alias Wedding is the slug of {TOPIC}",
        },
        {},
    ),
    "an alias that's another page's name": (
        lambda pages: (
            pages.update({"topics/kitchen-remodel.md": KITCHEN}),
            _edit(PERSON, "aliases: [Dave]", "aliases: [Dave, kitchen]")(pages),
            _edit(PERSON, "married on Oct 24", "married on Oct 24, [[kitchen-remodel]] or not")(
                pages
            ),
        ),
        {
            "kind": "duplicate",
            "pages": [PERSON, "topics/kitchen-remodel.md"],
            "path": PERSON,
            "message": "a duplicate candidate: its alias kitchen is the name of "
            "topics/kitchen-remodel.md",
        },
        {},
    ),
    "near-identical slugs": (
        lambda pages: (
            pages.update({"topics/weddings.md": KITCHEN}),
            _edit(PERSON, "married on Oct 24", "married on Oct 24, after [[weddings]]")(pages),
        ),
        {
            "kind": "duplicate",
            "pages": ["topics/weddings.md", TOPIC],
            "path": "topics/weddings.md",
            "message": f"a duplicate candidate: its slug is nearly {TOPIC}'s",
        },
        {},
    ),
    "a slug over the threshold with no page": (
        lambda pages: pages.pop(TOPIC),
        {
            "kind": "slug-without-page",
            "pages": [],
            "path": f"days/{ROOM_LIST_DAY}.md",
            "message": "`wedding` is linked on 2 days, reaching the page threshold of 2, but "
            "has no page",
            "slug": "wedding",
        },
        {"PA_LIBRARIAN_PAGE_THRESHOLD": "2"},
    ),
    "a needs-lookup marker": (
        _edit(PERSON, "Getting married on Oct 24", "[needs lookup: Lodge check-in time] Oct 24"),
        {
            "kind": "needs-lookup",
            "pages": [PERSON],
            "path": PERSON,
            "line": 12,
            "message": "needs lookup: Lodge check-in time",
        },
        {},
    ),
}


@pytest.mark.parametrize("kind", KINDS, ids=list(KINDS))
def test_every_lint_warning_is_offered_as_a_candidate_with_its_kind_and_pages(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str
):
    edit, candidate, environment = KINDS[kind]
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    pages = settled_pages()
    edit(pages)
    run_with_pages(data_root, pages)
    script(data_root, dreaming=[{"outcomes": [{"candidate": "c01", "outcome": "dismissed"}]}])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert session["candidates"] == [{"id": "c01", **candidate}]
    summary = commit_messages(data_root)[0].splitlines()[0]
    assert summary == "dreaming run: 1 candidates, 0 applied, 0 proposed, 1 dismissed"


def test_a_slug_at_the_threshold_is_a_candidate_only_once_the_owner_has_engaged_with_it(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_PAGE_THRESHOLD", "2")
    # Both slugs are linked on two days; only the wedding by mail the owner sent.
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/deposit.eml": email(
                message_id="wedding-0004@example.net",
                date=mailed(10, "08:00"),
                subject="Deposit",
            ),
            "gmail/room-list.eml": email(
                message_id="wedding-0002@example.net",
                labels="Sent",
                date=mailed(10, "09:00"),
                sender="Argus McNevans <argus@example.com>",
                to="David Kim <dkim@example.net>",
                subject="Room list",
            ),
        },
    )
    links = {"Hotel block": ["lodge", "wedding"], "Deposit": ["lodge"], "Room list": ["wedding"]}
    script(data_root, observation=[{"observe": True, "links": links}])
    assert run_librarian() == 0
    script(data_root, dreaming=[{"outcomes": [{"candidate": "c01", "outcome": "dismissed"}]}])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert [(c["kind"], c["slug"]) for c in session["candidates"]] == [
        ("slug-without-page", "wedding")
    ]


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def reporting(*outcomes: dict) -> dict:
    """The stand-in's action ending a session with `outcomes` in its final message."""
    return {"outcomes": list(outcomes)}


APPLIED = {"candidate": "c01", "outcome": "applied", "pages": [TOPIC]}
DISMISSED = {"candidate": "c01", "outcome": "dismissed"}

# What a first attempt reports about the wiki's one candidate, and what its resume is told.
INVALID_OUTCOMES = {
    "no outcomes at all": ([], "holds no outcomes"),
    "outcomes that aren't JSON": ([{"final": "```json\n{outcomes: c01\n```"}], "aren't JSON"),
    "an outcome that is none of applied, proposed, and dismissed": (
        [reporting({"candidate": "c01", "outcome": "fixed"})],
        "outcomes.0: Input tag 'fixed' found using 'outcome' does not match any of the "
        "expected tags: 'applied', 'proposed', 'dismissed'",
    ),
    "a field no outcome has": (
        [reporting({**DISMISSED, "because": "it is fine"})],
        "outcomes.0.dismissed.because: Extra inputs are not permitted",
    ),
    "a candidate without an outcome": ([reporting()], "c01 has no outcome"),
    "an outcome for no candidate": (
        [reporting(DISMISSED, {"candidate": "c09", "outcome": "dismissed"})],
        "c09 isn't one of this run's candidates",
    ),
    "two outcomes for one candidate": (
        [reporting(DISMISSED, DISMISSED)],
        "c01 has 2 outcomes",
    ),
    "an applied outcome that names no page": (
        [reporting({**APPLIED, "pages": []})],
        "c01 is applied but names no page",
    ),
    "an applied outcome naming a page that didn't change": (
        [reporting(APPLIED)],
        f"c01 names {TOPIC}, which this session didn't change",
    ),
}


@pytest.mark.parametrize("invalid", INVALID_OUTCOMES, ids=list(INVALID_OUTCOMES))
def test_missing_or_invalid_outcomes_resume_the_session_to_report_them(
    data_root: Path, invalid: str
):
    run_with_pages(data_root)
    first, told = INVALID_OUTCOMES[invalid]
    script(data_root, dreaming=first, dreaming_resume=[reporting(DISMISSED)])

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert "--resume" not in started["argv"]
    assert flag(resumed["argv"], "--resume") == "session-1"
    assert resumed["prompt"].startswith("Phase: dreaming\n")
    assert told in resumed["prompt"]
    summary, _, last = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 0 applied, 0 proposed, 1 dismissed"
    recorded = fields(last)
    # A resumed session's cost and turns add up over its attempts.
    assert recorded["Dreaming-Resumes"] == "1"
    assert recorded["Dreaming-Cost-USD"] == "0.0842"
    assert recorded["Dreaming-Turns"] == "12"


def test_the_outcomes_are_the_last_json_block_of_the_sessions_final_message(data_root: Path):
    run_with_pages(data_root)
    final = textwrap.dedent(
        """\
        I first meant to close it:

        ```json
        {"outcomes": [{"candidate": "c01", "outcome": "applied", "pages": ["topics/wedding.md"]}]}
        ```

        Nothing in L0 closes it, so:

        ```json
        {"outcomes": [{"candidate": "c01", "outcome": "dismissed"}]}
        ```

        ```
        No page changed.
        ```
        """
    )
    script(data_root, dreaming=[{"final": final}])

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    summary = commit_messages(data_root)[0].splitlines()[0]
    assert summary == "dreaming run: 1 candidates, 0 applied, 0 proposed, 1 dismissed"


def test_outcomes_still_invalid_after_two_resumes_fail_the_run_and_leave_the_wiki(
    data_root: Path, capsys
):
    room_list = run_with_pages(data_root)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    # The loop is closed, and nothing ever says so.
    script(data_root, dreaming=[{"write": TOPIC, "text": closed(room_list)}])
    capsys.readouterr()

    assert dream() != 0

    attempts = sessions(data_root, "dreaming")
    assert ["--resume" in s["argv"] for s in attempts] == [False, True, True]
    error = run_error(data_root)
    assert "holds no outcomes" in error
    assert "after 2 resumes" in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


LOOP_CLOSED = {"write": TOPIC, "text": closed(HOTEL)}
"""The session closing the wedding's loop, which its applied outcome claims."""
RETITLED = wedding_pages()[PERSON].replace("# David Kim", "# Dave Kim")

# What a dreaming session does, the reason the run gives for failing, and the session's
# attempts: a session out of bounds isn't resumed.
FAILURES = {
    "a line added to a day log": (
        [
            LOOP_CLOSED,
            {"append": f"days/{HOTEL_DAY}.md", "text": f"- [[wedding]] again [ep:{HOTEL}]\n"},
            reporting(APPLIED),
        ],
        f"the dreaming session changed days/{HOTEL_DAY}.md, outside the pages",
        1,
    ),
    "a new day log": (
        [
            {"write": f"days/{days_ago(9)}.md", "text": f"- [[wedding]] again [ep:{HOTEL}]\n"},
            reporting(DISMISSED),
        ],
        f"the dreaming session changed days/{days_ago(9)}.md, outside the pages",
        1,
    ),
    "the index written": (
        [{"write": "index.md", "text": "# Index\n"}, reporting(DISMISSED)],
        "the dreaming session changed index.md, outside the pages",
        1,
    ),
    "a changed page no outcome claims": (
        [LOOP_CLOSED, {"write": PERSON, "text": RETITLED}, reporting(APPLIED)],
        f"the dreaming session changed {PERSON}, which no outcome claims",
        1,
    ),
    "a new page no outcome claims": (
        [{"write": "topics/kitchen.md", "text": KITCHEN}, reporting(DISMISSED)],
        "the dreaming session changed topics/kitchen.md, which no outcome claims",
        1,
    ),
    "a session error": ([{"fail": "API Error: overloaded"}], "API Error: overloaded", 1),
    "a page that still fails lint after two resumes": (
        [
            {"write": TOPIC, "text": closed(HOTEL).replace("· asked", "· begged")},
            reporting(APPLIED),
        ],
        "the dreaming session left 1 lint errors after 2 resumes",
        3,
    ),
}


@pytest.mark.parametrize("failure", FAILURES, ids=list(FAILURES))
def test_a_failed_dreaming_run_leaves_the_wiki_at_its_last_commit(
    data_root: Path, capsys, failure: str
):
    run_with_pages(data_root)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    actions, reason, attempts = FAILURES[failure]
    # A resumed session reports the same outcomes, and fixes nothing.
    script(data_root, dreaming=actions, dreaming_resume=actions[-1:])
    capsys.readouterr()

    assert dream() != 0

    # The command says where the run failed, and nothing a page or the session said: the
    # whole error is kept under the data root, for the owner.
    kept = data_root / "run-errors" / "dreaming-run.txt"
    assert capsys.readouterr().err == (
        f"pa: the run failed in its dreaming session and was discarded; its error is in {kept}\n"
    )
    error = run_error(data_root)
    assert reason in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert kept.stat().st_mode & 0o777 == 0o600
    assert len(sessions(data_root, "dreaming")) == attempts
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history
    # The next nightly run carries on from the same cursor.
    ingest(
        data_root,
        {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")},
    )
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["Gift"]


def test_a_dreaming_session_that_leaves_lint_errors_is_resumed_with_the_findings(
    data_root: Path,
):
    room_list = run_with_pages(data_root)
    malformed = closed(room_list).replace("· asked", "· begged")
    script(
        data_root,
        dreaming=[{"write": TOPIC, "text": malformed}, reporting(APPLIED)],
        dreaming_resume=[{"write": TOPIC, "text": closed(room_list)}, reporting(APPLIED)],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert flag(resumed["argv"], "--resume") == "session-1"
    assert f"{TOPIC}:16: malformed loop" in resumed["prompt"]
    # Only its last message's outcomes count, so it is asked for them again.
    assert "end your reply with the outcomes again" in resumed["prompt"]
    assert wiki_files(data_root)[TOPIC] == closed(room_list)
    recorded = fields(commit_messages(data_root)[0])
    assert (recorded["Dreaming-Resumes"], recorded["Dreaming-Applied"]) == ("1", "1")


DAVE = textwrap.dedent(
    f"""\
    ---
    type: person
    aliases: []
    status: active
    summary: Dave, who sent the room list.
    updated: {ROOM_LIST_DAY}
    emails: []
    ---
    # Dave

    ## Current state
    Sent the room list [ep:{HOTEL}].

    ## Open loops

    ## Notes
    """
)
"""A second page for David Kim, under the name his own page has as an alias."""


def test_the_host_removes_a_merged_page_rewrites_its_links_and_regenerates_the_index(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    # The later email, the owner's own, is observed as to [[dave]], who has a page beside
    # David Kim's: the owner has engaged with both.
    monkeypatch.setitem(LINKS, "Room list", ["dave", "david-kim", "wedding"])
    pages = {**settled_pages(), "people/dave.md": DAVE}
    room_list = run_with_pages(data_root, pages)
    assert "- [[dave]]: Dave, who sent the room list." in wiki_files(data_root)["index.md"]
    # L0 shows they are one: David Kim's page takes Dave's line, and Dave's page is emptied,
    # since a session's file tools can't remove a file.
    merged = pages[PERSON].replace(
        "## Notes\n", f"## Notes\nGoes by Dave, who sent the room list [ep:{room_list}].\n"
    )
    script(
        data_root,
        dreaming=[
            {"write": "people/dave.md", "text": ""},
            {"write": PERSON, "text": merged},
            reporting({**APPLIED, "pages": ["people/dave.md", PERSON]}),
        ],
    )

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert session["candidates"] == [
        {
            "id": "c01",
            "kind": "duplicate",
            "pages": [PERSON, "people/dave.md"],
            "path": PERSON,
            "message": "a duplicate candidate: its alias Dave is the slug of people/dave.md",
        }
    ]
    files = wiki_files(data_root)
    assert "people/dave.md" not in files
    assert files[PERSON] == merged
    # The day log's link now goes to the page that took the name, and the index follows.
    assert files[f"days/{ROOM_LIST_DAY}.md"] == (
        "- email about Room list with [[david-kim|dave]] and [[david-kim]] and [[wedding]] "
        f"[ep:{room_list}]\n"
    )
    assert "dave" not in files["index.md"].replace("david-kim", "")
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    summary, listed, _ = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 1 applied, 0 proposed, 0 dismissed"
    assert listed.splitlines()[1] == (
        f"- c01 · duplicate · {PERSON}, people/dave.md · applied: people/dave.md, {PERSON}"
    )


def test_an_emptied_page_no_merge_explains_fails_the_dreaming_run(data_root: Path, capsys):
    run_with_pages(data_root)
    before = tree_state(data_root / "wiki")
    emptied = [{"write": TOPIC, "text": ""}, reporting(APPLIED)]
    script(data_root, dreaming=emptied, dreaming_resume=emptied)
    capsys.readouterr()

    assert dream() != 0

    assert f"{TOPIC}:1: bad frontmatter" in run_error(data_root)
    assert tree_state(data_root / "wiki") == before


def test_the_dreaming_session_gets_the_nightly_sessions_tools_and_settings(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_MAX_TURNS", "40")
    monkeypatch.setenv("PA_LIBRARIAN_EFFORT", "high")
    run_with_pages(data_root)
    script(data_root, dreaming=[reporting(DISMISSED)])

    assert dream() == 0

    [nightly] = sessions(data_root, "consolidation")
    [session] = sessions(data_root, "dreaming")
    argv = session["argv"]
    # Only the file tools, and the rendered project's settings, attaching L0 and the wiki.
    assert set(flag(argv, "--tools").split(",")) == {"Read", "Edit", "Write", "Glob", "Grep"}
    assert "--add-dir" not in argv
    assert session["settings"]["permissions"]["additionalDirectories"] == [
        str(data_root / "l0"),
        str(data_root / "wiki"),
    ]
    assert Path(flag(argv, "--settings")).is_relative_to(session["cwd"])
    # Everything a nightly session is started with, but its own prompt, rendered project, and
    # "now".
    assert session["settings"] == nightly["settings"]
    assert session["claude_md"] == nightly["claude_md"]
    assert "\n## Dreaming\n" in session["claude_md"] and "Argus McNevans" in session["claude_md"]
    for name in ("--tools", "--permission-mode", "--model", "--effort", "--max-turns"):
        assert flag(argv, name) == flag(nightly["argv"], name)
    assert [a for a in argv if a.startswith("-")] == [
        a for a in nightly["argv"] if a.startswith("-")
    ]
    assert (flag(argv, "--max-turns"), flag(argv, "--effort")) == ("40", "high")
    # Now is the clock's time, in the owner's time zone.
    stamp = re.search(r"; (\S+)\)\. Treat it as now\.", flag(argv, "--append-system-prompt"))
    assert stamp is not None
    told = datetime.fromisoformat(stamp[1])
    chicago = datetime.now(CHICAGO)
    assert told.utcoffset() == chicago.utcoffset()
    assert abs(chicago - told) < timedelta(minutes=1)
    # Its prompt names its phase, and says where it may read and write.
    assert session["prompt"].startswith("Phase: dreaming\n")
    assert f"`{data_root / 'l0'}`" in session["prompt"]
    assert f"`{data_root / 'wiki' / 'topics'}`" in session["prompt"]


def test_pa_librarian_dream_skips_while_another_job_holds_the_lock(data_root: Path, capsys):
    run_with_pages(data_root)
    history = commit_messages(data_root)
    capsys.readouterr()

    with data_root_lock(DataRoot(data_root)):
        assert dream() == 0

    out = capsys.readouterr().out
    assert out == "librarian dream: skipped: another job holds the data root's lock\n"
    assert sessions(data_root, "dreaming") == []
    assert commit_messages(data_root) == history


# Stale pages, statuses that disagree with their loops, and quiet pages (#145): what dreaming
# sees that lint doesn't.


def _each(*edits):
    def edit(pages: dict[str, str]) -> None:
        for one in edits:
            one(pages)

    return edit


OPEN = ROOM_LIST.replace(f"asked {days_ago(40)}", f"asked {days_ago(1)}")
"""The wedding's loop as the settled pages have it: open since yesterday, owed by Dave."""
RSVP = f"- [ ] me → [[david-kim]]: an RSVP · promised {days_ago(1)} [ep:{HOTEL}]"
"""A loop for Dave's own page."""
MISMATCH = "a status that disagrees with its loops: "


def _kitchen(updated: str, status: str = "active"):
    """The kitchen's page added, last updated on `updated`."""

    def edit(pages: dict[str, str]) -> None:
        pages["topics/kitchen.md"] = KITCHEN.replace(
            f"updated: {ROOM_LIST_DAY}", f"updated: {updated}"
        ).replace("status: active", f"status: {status}")

    return edit


# Each wiki with such a problem: what puts it in the settled pages, what the day logs also
# hold, the candidate the session is given for it (after its id, `c01`), and the environment
# that brings it out.
FLAGGED = {
    "a page linked on three days since its update": (
        lambda pages: None,
        # Dave is linked on two of them, and on a day yet to come: a dinner booked ahead.
        [
            linked(days_ago(8), "wedding", "david-kim"),
            linked(days_ago(5), "wedding", "david-kim"),
            linked(days_ago(3), "wedding"),
            linked(days_ago(-4), "david-kim"),
        ],
        {
            "kind": "stale",
            "pages": [TOPIC],
            "path": TOPIC,
            "message": f"a stale page: updated {ROOM_LIST_DAY}, and observations link "
            "[[wedding]] on 3 later days",
            "day_logs": [f"days/{days_ago(days)}.md" for days in (8, 5, 3)],
        },
        {},
    ),
    "a page linked on one later day, at a threshold of one": (
        lambda pages: None,
        [linked(days_ago(8), "wedding")],
        {
            "kind": "stale",
            "pages": [TOPIC],
            "path": TOPIC,
            "message": f"a stale page: updated {ROOM_LIST_DAY}, and observations link "
            "[[wedding]] on 1 later day",
            "day_logs": [f"days/{days_ago(8)}.md"],
        },
        {"PA_LIBRARIAN_STALE_LATER_DAYS": "1"},
    ),
    "a settled topic with an open loop": (
        _edit(TOPIC, "status: active", "status: settled"),
        [],
        {
            "kind": "status-mismatch",
            "pages": [TOPIC],
            "path": TOPIC,
            "message": f"{MISMATCH}it is settled, with 1 open loop on {TOPIC}",
        },
        {},
    ),
    "a dormant topic with an open loop": (
        _edit(TOPIC, "status: active", "status: dormant"),
        [],
        {
            "kind": "status-mismatch",
            "pages": [TOPIC],
            "path": TOPIC,
            "message": f"{MISMATCH}it is dormant, with 1 open loop on {TOPIC}",
        },
        {},
    ),
    "an inactive person who owes an open loop on a topic's page": (
        _edit(PERSON, "status: active", "status: inactive"),
        [],
        {
            "kind": "status-mismatch",
            "pages": [PERSON, TOPIC],
            "path": PERSON,
            "message": f"{MISMATCH}it is inactive, with 1 open loop on {TOPIC}",
        },
        {},
    ),
    "an inactive person with open loops on their own page and a topic's": (
        _each(
            _edit(PERSON, "status: active", "status: inactive"),
            _edit(PERSON, "## Open loops\n", f"## Open loops\n{RSVP}\n"),
        ),
        [],
        {
            "kind": "status-mismatch",
            "pages": [PERSON, TOPIC],
            "path": PERSON,
            "message": f"{MISMATCH}it is inactive, with 2 open loops on {PERSON}, {TOPIC}",
        },
        {},
    ),
    "an active page nothing has linked for 90 days": (
        _kitchen(days_ago(90)),
        # A visit booked ahead is logged on its day, which is yet to come.
        [
            linked(days_ago(120), "kitchen"),
            linked(days_ago(90), "kitchen"),
            linked(days_ago(-3), "kitchen"),
        ],
        {
            "kind": "quiet",
            "pages": ["topics/kitchen.md"],
            "path": "topics/kitchen.md",
            "message": "a quiet page: it is active, and no observation has linked "
            f"[[kitchen]] since {days_ago(90)}, 90 days ago",
        },
        {},
    ),
    "an active page nothing has linked for as long as configured": (
        _kitchen(days_ago(11)),
        [linked(days_ago(11), "kitchen")],
        {
            "kind": "quiet",
            "pages": ["topics/kitchen.md"],
            "path": "topics/kitchen.md",
            "message": "a quiet page: it is active, and no observation has linked "
            f"[[kitchen]] since {days_ago(11)}, 11 days ago",
        },
        {"PA_LIBRARIAN_QUIET_DAYS": "11"},
    ),
}

BOTH = ("wedding", "david-kim")


def _updated(day: str):
    """Every page last updated on `day`."""

    def edit(pages: dict[str, str]) -> None:
        for path in pages:
            _edit(path, f"updated: {ROOM_LIST_DAY}", f"updated: {day}")(pages)

    return edit


# Wikis with no such problem: what changes the settled pages, what the day logs also hold, and
# the environment.
NOT_FLAGGED = {
    "pages linked on two days since their update": (
        lambda pages: None,
        [linked(days_ago(8), *BOTH), linked(days_ago(5), *BOTH)],
        {},
    ),
    "pages updated on their latest linking day": (
        _updated(days_ago(3)),
        [linked(days_ago(8), *BOTH), linked(days_ago(5), *BOTH), linked(days_ago(3), *BOTH)],
        {},
    ),
    "pages updated after their latest linking day": (
        _updated(days_ago(1)),
        [linked(days_ago(8), *BOTH), linked(days_ago(5), *BOTH), linked(days_ago(3), *BOTH)],
        {},
    ),
    # Day logs exist for days yet to come: calendar events booked ahead.
    "pages linked on three days after today": (
        lambda pages: None,
        [linked(days_ago(-1), *BOTH), linked(days_ago(-2), *BOTH), linked(days_ago(-9), *BOTH)],
        {},
    ),
    "pages updated on their latest linking day, at a threshold of one": (
        _updated(days_ago(3)),
        [linked(days_ago(8), *BOTH), linked(days_ago(3), *BOTH), linked(days_ago(-1), *BOTH)],
        {"PA_LIBRARIAN_STALE_LATER_DAYS": "1"},
    ),
    "a settled topic and an inactive person whose loops are closed": (
        _each(
            _edit(TOPIC, "status: active", "status: settled"),
            _edit(PERSON, "status: active", "status: inactive"),
            _edit(PERSON, "## Open loops\n", f"## Open loops\n{RSVP}\n"),
            *(
                _edit(
                    page,
                    loop,
                    loop.replace("- [ ]", "- [x]") + f" · closed {days_ago(0)} [ep:{HOTEL}]",
                )
                for page, loop in ((TOPIC, OPEN), (PERSON, RSVP))
            ),
        ),
        [],
        {},
    ),
    "an active page linked 89 days ago": (
        _kitchen(days_ago(89)),
        [linked(days_ago(120), "kitchen"), linked(days_ago(89), "kitchen")],
        {},
    ),
    "a settled topic nothing has linked for 120 days": (
        _kitchen(days_ago(120), status="settled"),
        [linked(days_ago(120), "kitchen")],
        {},
    ),
    # Nothing has linked it yet, so there is nothing to measure its quiet from.
    "an active page linked only on a day after today": (
        _kitchen(days_ago(120)),
        [linked(days_ago(-3), "kitchen")],
        {},
    ),
}


@pytest.mark.parametrize("wiki", NOT_FLAGGED, ids=list(NOT_FLAGGED))
def test_a_wiki_with_no_stale_page_mismatched_status_or_quiet_page_offers_no_candidate(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, wiki: str
):
    edit, logged, environment = NOT_FLAGGED[wiki]
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    pages = settled_pages()
    edit(pages)
    run_with_pages(data_root, pages, logged)
    capsys.readouterr()

    assert dream() == 0

    assert sessions(data_root, "dreaming") == []
    assert "librarian dream: no candidates in the wiki" in capsys.readouterr().out


@pytest.mark.parametrize("problem", FLAGGED, ids=list(FLAGGED))
def test_a_stale_page_a_status_that_disagrees_with_its_loops_and_a_quiet_page_are_candidates(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, problem: str
):
    edit, logged, candidate, environment = FLAGGED[problem]
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    pages = settled_pages()
    edit(pages)
    run_with_pages(data_root, pages, logged)
    script(data_root, dreaming=[reporting(DISMISSED)])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert session["candidates"] == [{"id": "c01", **candidate}]
    summary = commit_messages(data_root)[0].splitlines()[0]
    assert summary == "dreaming run: 1 candidates, 0 applied, 0 proposed, 1 dismissed"


def test_candidates_are_offered_in_a_fixed_order_of_kinds(data_root: Path):
    # The wedding's page is stale and its loop is aging, Dave is inactive though he owes that
    # loop, and nothing has linked the kitchen in 90 days.
    pages = wedding_pages()
    _each(_edit(PERSON, "status: active", "status: inactive"), _kitchen(days_ago(90)))(pages)
    logged = [
        linked(days_ago(90), "kitchen"),
        *(linked(days_ago(days), "wedding") for days in (8, 5, 3)),
    ]
    run_with_pages(data_root, pages, logged)
    judged = [{"candidate": f"c0{number}", "outcome": "dismissed"} for number in (1, 2, 3, 4)]
    script(data_root, dreaming=[reporting(*judged)])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert [(c["id"], c["kind"], c["pages"]) for c in session["candidates"]] == [
        ("c01", "stale", [TOPIC]),
        ("c02", "status-mismatch", [PERSON, TOPIC]),
        ("c03", "loop-aging", [TOPIC]),
        ("c04", "quiet", ["topics/kitchen.md"]),
    ]
    assert commit_messages(data_root)[0].split("\n\n")[1].splitlines() == [
        "Candidates:",
        f"- c01 · stale · {TOPIC} · dismissed",
        f"- c02 · status-mismatch · {PERSON}, {TOPIC} · dismissed",
        f"- c03 · loop-aging · {TOPIC} [ep:{HOTEL}] · dismissed",
        "- c04 · quiet · topics/kitchen.md · dismissed",
    ]


def test_a_stale_page_brought_up_to_date_is_committed_and_claimed_by_its_outcome(
    data_root: Path,
):
    later = [linked(days_ago(days), "wedding") for days in (8, 5, 3)]
    run_with_pages(data_root, settled_pages(), later)
    history = commit_messages(data_root)
    current = (
        settled_pages()[TOPIC]
        .replace(f"updated: {ROOM_LIST_DAY}", f"updated: {days_ago(0)}")
        .replace("The hotel block closes on Oct 9", "The hotel block is full")
    )
    script(data_root, dreaming=[{"write": TOPIC, "text": current}, reporting(APPLIED)])

    assert dream() == 0

    assert wiki_files(data_root)[TOPIC] == current
    assert git(data_root, "show", "--format=", "--name-only", "HEAD").split() == [TOPIC]
    summary, listed, _ = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 1 applied, 0 proposed, 0 dismissed"
    assert listed.splitlines() == ["Candidates:", f"- c01 · stale · {TOPIC} · applied: {TOPIC}"]
    assert commit_messages(data_root)[1:] == history
    # Updated after everything that links it, the page is no longer a candidate.
    assert dream() == 0
    assert len(sessions(data_root, "dreaming")) == 1
    assert len(commit_messages(data_root)) == len(history) + 1


def judging(kind: str) -> str:
    """How the instructions say to judge a `kind` candidate: its bullet under "How to judge
    each kind", with its whitespace collapsed."""
    bullet = re.search(
        rf"^- \*\*`{kind}`\*\*.*?(?=^- \*\*`|^### )", dreaming_instructions(), re.M | re.S
    )
    assert bullet is not None, f"no bullet says how to judge a {kind} candidate"
    return " ".join(bullet[0].split())


def test_the_instructions_say_how_to_judge_the_three_kinds_lint_has_no_warning_for():
    # A stale page is brought up to date from the day logs its candidate lists.
    stale = judging("stale")
    assert "The candidate lists those `day_logs`" in stale
    assert "Bring the page up to date from them" in stale
    # A status is corrected in whichever direction L0 supports.
    mismatch = judging("status-mismatch")
    assert "close the loop" in mismatch
    assert "set the page's `status` to `active`" in mismatch
    # A quiet page is marked dormant or inactive.
    quiet = judging("quiet")
    assert "`dormant`" in quiet and "`inactive`" in quiet


def dreaming_instructions() -> str:
    """The librarian project's instructions for the dreaming phase: CLAUDE.md's Dreaming
    section, as the checkout has it (a session runs the project as committed)."""
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    section = re.search(r"^## Dreaming\n(.*?)(?=^## )", claude_md, re.M | re.S)
    assert section is not None, "CLAUDE.md has no Dreaming section"
    return section[1]


def test_the_instructions_name_the_dreaming_phase_and_how_to_judge_every_kind_of_candidate():
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    instructions = dreaming_instructions()

    assert "`Phase: dreaming`" in claude_md.split("\n## ")[0]
    for kind in CandidateKind:
        assert f"`{kind}`" in instructions, f"nothing says how to judge a {kind} candidate"
    for outcome in OUTCOMES:
        assert f"**{outcome}**" in instructions


def test_the_instructions_example_outcomes_are_in_the_shape_the_host_accepts():
    [example] = re.findall(r"^```json\n(.*?)^```$", dreaming_instructions(), re.M | re.S)

    outcomes = Outcomes.model_validate_json(example).outcomes

    assert {type(outcome) for outcome in outcomes} == {Applied, Proposed, Dismissed}
