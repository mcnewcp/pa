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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.librarian.candidates import CandidateKind
from pa_core.librarian.dreaming import Applied, Dismissed, Outcomes
from pa_home.cli import main
from pa_home.config import DataRoot
from pa_home.cut import take_cut
from pa_home.librarian_project import LIBRARIAN_PROJECT
from pa_home.lock import data_root_lock

# Sent late on 30 Sep in Chicago, which is already 1 Oct in UTC.
HOTEL = "gmail_9ff7394d0df8584d"
"""The id of the episode `email()` makes by default."""


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    date: str = "Wed, 30 Sep 2026 21:30:00 -0500",
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


def days_ago(days: int) -> str:
    """The date `days` days before today in the owner's time zone; runs are told the clock."""
    return (datetime.now(ZoneInfo("America/Chicago")).date() - timedelta(days=days)).isoformat()


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
            updated: 2026-10-02
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
            updated: 2026-10-02
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


def run_with_pages(data_root: Path, pages: dict[str, str] | None = None) -> str:
    """One nightly run over the hotel email and a later one sending the room list, whose
    consolidation writes `pages` (the wedding pages unless given). Returns the later email's
    episode id."""
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/room-list.eml": email(
                message_id="wedding-0002@example.net",
                # The owner's own mail carries the Sent label; without it, it's forged, and
                # the owner hasn't engaged with what it links (#142).
                labels="Sent",
                date="Fri, 2 Oct 2026 09:00:00 -0500",
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
        observation=[{"observe": True, "links": LINKS}],
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
    updated: 2026-10-02
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
    assert summary == "dreaming run: 2 candidates, 1 applied, 1 dismissed"
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
    assert "judged 2 candidates (1 applied, 1 dismissed)" in out
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
            "path": "days/2026-10-02.md",
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
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 dismissed"


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
    "an outcome that is neither applied nor dismissed": (
        [reporting({"candidate": "c01", "outcome": "fixed"})],
        "outcomes.0: Input tag 'fixed' found using 'outcome' does not match any of the "
        "expected tags: 'applied', 'dismissed'",
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
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 dismissed"
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
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 dismissed"


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
    error = capsys.readouterr().err
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
            {"append": "days/2026-09-30.md", "text": f"- [[wedding]] again [ep:{HOTEL}]\n"},
            reporting(APPLIED),
        ],
        "the dreaming session changed days/2026-09-30.md, outside the pages",
        1,
    ),
    "a new day log": (
        [
            {"write": "days/2026-10-03.md", "text": f"- [[wedding]] again [ep:{HOTEL}]\n"},
            reporting(DISMISSED),
        ],
        "the dreaming session changed days/2026-10-03.md, outside the pages",
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

    error = capsys.readouterr().err
    assert reason in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
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
    updated: 2026-10-02
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
    # The later email is observed as from [[dave]], who has a page beside David Kim's.
    monkeypatch.setitem(LINKS, "Room list", ["dave", "wedding"])
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
    assert files["days/2026-10-02.md"] == (
        f"- email about Room list with [[david-kim|dave]] and [[wedding]] [ep:{room_list}]\n"
    )
    assert "dave" not in files["index.md"].replace("david-kim", "")
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    summary, listed, _ = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 1 applied, 0 dismissed"
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

    assert f"{TOPIC}:1: bad frontmatter" in capsys.readouterr().err
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
    chicago = datetime.now(ZoneInfo("America/Chicago"))
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
    for outcome in ("applied", "dismissed"):
        assert f"**{outcome}**" in instructions


def test_the_instructions_example_outcomes_are_in_the_shape_the_host_accepts():
    [example] = re.findall(r"^```json\n(.*?)^```$", dreaming_instructions(), re.M | re.S)

    outcomes = Outcomes.model_validate_json(example).outcomes

    assert {type(outcome) for outcome in outcomes} == {Applied, Dismissed}
