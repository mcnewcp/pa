"""Retirement: dreaming clears the pages the owner has no use for, and they stay cleared (#147).

Whole runs of `pa librarian dream` and `pa librarian run` over a temporary data root, through
the stand-in `claude` (the conftest at the repository root says what it does and how it's
scripted). Each test builds a wiki with a nightly run (`run_with_pages`), scripts the sessions'
writes and the outcomes a dreaming session reports, and asserts on the wiki, its git history,
and what the sessions were given.

The wiki is David Kim's page and his wedding's, which the owner has written about, and a page
for Nina Park, a realtor who wrote to the owner once and never heard back.
"""

import json
import os
import subprocess
import textwrap
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


# The wiki's mail is dated by the clock, as the runs are: a page's age is measured from today,
# and one nothing has linked for 90 days is a candidate of another kind.
TODAY = days_ago(0)
UPDATED = days_ago(10)
"""The day the wiki's pages say they last changed: the day of its latest mail."""
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


def sent(subject: str, days: int, message_id: str, to: str = "David Kim <dkim@example.net>"):
    """Mail the owner sent `days` days ago: the Sent label is what makes it the owner's own."""
    return email(
        message_id=message_id,
        labels="Sent",
        date=mailed(days, "09:00"),
        sender="Argus McNevans <argus@example.com>",
        to=to,
        subject=subject,
        body="Thanks.",
    )


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


def page(
    kind: str,
    title: str,
    *,
    aliases: str = "[]",
    state: str = "Wrote about a listing",
    loops: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> str:
    """A page of `kind` (person, organization, or topic) named `title`, with nothing for lint
    to warn about unless `loops` or `notes` bring it."""
    identifiers = {"person": "emails: []\n", "organization": "domains: []\n", "topic": ""}[kind]
    decisions = "## Decisions\n\n" if kind == "topic" else ""
    return (
        "---\n"
        f"type: {kind}\n"
        f"aliases: {aliases}\n"
        "status: active\n"
        f"summary: {title}, as the mail has it.\n"
        f"updated: {UPDATED}\n"
        f"{identifiers}"
        "---\n"
        f"# {title}\n\n"
        f"## Current state\n{state} [ep:{HOTEL}].\n\n"
        f"{decisions}"
        "## Open loops\n" + "".join(f"{line}\n" for line in loops) + "\n"
        "## Notes\n" + "".join(f"{line}\n" for line in notes)
    )


DAVID, WEDDING, NINA = "people/david-kim.md", "topics/wedding.md", "people/nina-park.md"
LAKEVIEW = "orgs/lakeview-realty.md"


def pages() -> dict[str, str]:
    """David Kim's page and his wedding's, which the owner has written about, and Nina Park's,
    who only ever wrote to the owner. Every page is linked from a day log."""
    return {
        DAVID: page("person", "David Kim", state="Getting married on Oct 24"),
        WEDDING: page("topic", "Wedding", state="The hotel block closes on Oct 9"),
        NINA: page("person", "Nina Park"),
    }


LINKS = {
    "Hotel block": ["david-kim", "wedding"],
    "Room list": ["david-kim", "wedding"],
    "Listing": ["nina-park", "lakeview-realty"],
}
"""The slugs the stand-in's observations link, by the episode's subject."""


def listing(days: int = 11, message_id: str = "listing-0001@lakeview.example") -> bytes:
    """Mail from Nina Park, of Lakeview Realty, which the owner never asked for."""
    return email(
        message_id=message_id,
        date=mailed(days, "08:00"),
        sender="Nina Park <nina@lakeview.example>",
        subject="Listing",
        body="A new listing near you.",
    )


def run_with_pages(
    data_root: Path, written: dict[str, str] | None = None, links: dict[str, list[str]] = LINKS
) -> None:
    """One nightly run over mail from David Kim, the owner's reply to him, and mail from Nina
    Park, whose observations link `links` and whose consolidation writes `written` (`pages()`
    unless given)."""
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/listing.eml": listing(),
            "gmail/room-list.eml": sent("Room list", 10, "wedding-0002@example.net"),
        },
    )
    written = pages() if written is None else written
    script(
        data_root,
        observation=[{"observe": True, "links": links}],
        consolidation=[{"write": path, "text": text} for path, text in written.items()],
    )
    assert run_librarian() == 0


def reporting(*outcomes: dict) -> dict:
    """The stand-in's action ending a session with `outcomes` in its final message."""
    return {"outcomes": list(outcomes)}


def dismissed(candidate: str = "c01") -> dict:
    return {"candidate": candidate, "outcome": "dismissed"}


UNENGAGED = (
    "an unengaged page: no observation linking [[{slug}]] cites an episode the owner sent, "
    "wrote, organized, or attends"
)


def test_a_person_or_organization_page_the_owner_never_engaged_with_is_a_candidate(
    data_root: Path,
):
    # A topic nobody but Nina has written about is no candidate: only people and
    # organizations are judged by engagement.
    written = {
        **pages(),
        LAKEVIEW: page("organization", "Lakeview Realty"),
        "topics/listing.md": page("topic", "Listing", notes=[f"From [[nina-park]] [ep:{HOTEL}]."]),
    }
    links = {**LINKS, "Listing": ["nina-park", "lakeview-realty", "listing"]}
    run_with_pages(data_root, written, links)
    script(data_root, dreaming=[reporting(dismissed("c01"), dismissed("c02"))])

    assert dream() == 0

    # David Kim, whom the owner wrote to, isn't offered.
    [session] = sessions(data_root, "dreaming")
    assert session["candidates"] == [
        {
            "id": "c01",
            "kind": "unengaged",
            "pages": [NINA],
            "path": NINA,
            "message": UNENGAGED.format(slug="nina-park"),
        },
        {
            "id": "c02",
            "kind": "unengaged",
            "pages": [LAKEVIEW],
            "path": LAKEVIEW,
            "message": UNENGAGED.format(slug="lakeview-realty"),
        },
    ]
    listed = commit_messages(data_root)[0].split("\n\n")[1].splitlines()
    assert listed == [
        "Candidates:",
        f"- c01 · unengaged · {NINA} · dismissed",
        f"- c02 · unengaged · {LAKEVIEW} · dismissed",
    ]


def test_a_page_stops_being_a_candidate_once_the_owner_engages_with_it(data_root: Path, capsys):
    run_with_pages(data_root)
    # The owner writes back to Nina.
    reply = sent("Viewing", 1, "listing-0002@example.com", to="Nina Park <nina@lakeview.example>")
    ingest(data_root, {"gmail/viewing.eml": reply})
    script(data_root, observation=[{"observe": True, "links": {"Viewing": ["nina-park"]}}])
    assert run_librarian() == 0
    capsys.readouterr()

    assert dream() == 0

    assert sessions(data_root, "dreaming") == []
    assert "librarian dream: no candidates in the wiki" in capsys.readouterr().out


RETIRED = "retired.md"
EMPTIED = {"write": NINA, "text": ""}
"""The session retiring Nina's page: its file tools can't remove a file, so it empties it."""


def retired_file(*lines: str) -> str:
    """`retired.md` as the host writes it, holding `lines`."""
    return "# Retired\n\n" + "".join(f"{line}\n" for line in lines)


def retiring(*slugs: str, pages: Sequence[str] = (NINA,), candidate: str = "c01") -> dict:
    """An applied outcome that changed `pages` and retired `slugs` (Nina's unless given)."""
    return {
        "candidate": candidate,
        "outcome": "applied",
        "pages": list(pages),
        "retired": list(slugs or ("nina-park",)),
    }


def day_logs(data_root: Path) -> dict[str, str]:
    return {path: text for path, text in wiki_files(data_root).items() if path.startswith("days/")}


def test_a_retirement_removes_the_page_and_lists_the_slug_in_retired_md_in_the_dreaming_commit(
    data_root: Path,
):
    run_with_pages(data_root)
    history, logged = commit_messages(data_root), day_logs(data_root)
    script(data_root, dreaming=[EMPTIED, reporting(retiring())])

    assert dream() == 0

    files = wiki_files(data_root)
    assert NINA not in files
    assert files[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")
    assert "nina-park" not in files["index.md"]
    # The day logs keep every fact, and still link her as they did.
    assert day_logs(data_root) == logged
    assert "[[nina-park]]" in "".join(logged.values())
    # One commit holds the removal and the list.
    assert commit_messages(data_root)[1:] == history
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert sorted(changed) == ["index.md", NINA, RETIRED]
    summary, listed, _ = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 1 applied, 0 proposed, 0 dismissed"
    assert listed.splitlines() == ["Candidates:", f"- c01 · unengaged · {NINA} · applied: {NINA}"]


def test_an_emptied_page_that_no_outcome_names_as_retired_fails_the_run(data_root: Path, capsys):
    run_with_pages(data_root)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    # The outcome claims the page, and doesn't say it was retired.
    claimed = reporting({"candidate": "c01", "outcome": "applied", "pages": [NINA]})
    script(data_root, dreaming=[EMPTIED, claimed], dreaming_resume=[claimed])
    capsys.readouterr()

    assert dream() != 0

    error = capsys.readouterr().err
    assert f"{NINA}:1: bad frontmatter" in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


REWORDED = page("person", "Nina Park", state="Sent a listing")

# What a first attempt does about Nina's page beside reporting it retired, and what its resume
# is told. The resume empties the page and reports the same.
INVALID_RETIREMENTS = {
    "a slug that had no page": (
        [EMPTIED, reporting(retiring("nina"))],
        "c01 retires `nina`, which had no page when this run started",
    ),
    "a page that still has content": (
        [{"write": NINA, "text": REWORDED}, reporting(retiring())],
        f"c01 retires `nina-park`, but {NINA} still has content",
    ),
}


@pytest.mark.parametrize("invalid", INVALID_RETIREMENTS, ids=list(INVALID_RETIREMENTS))
def test_a_retirement_the_host_cant_carry_out_resumes_the_session(data_root: Path, invalid: str):
    run_with_pages(data_root)
    first, told = INVALID_RETIREMENTS[invalid]
    script(data_root, dreaming=first, dreaming_resume=[EMPTIED, reporting(retiring())])

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert told in resumed["prompt"]
    files = wiki_files(data_root)
    assert NINA not in files
    assert files[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")
    assert fields(commit_messages(data_root)[0])["Dreaming-Resumes"] == "1"


ASK = f"- [ ] me → [[nina-park]]: a reply about the listing · asked {days_ago(1)} [ep:{HOTEL}]"
"""An open loop on Nina's page."""


def pages_with_a_loop() -> dict[str, str]:
    """The pages, with an open loop on Nina's and a page for her agency."""
    return {
        **pages(),
        NINA: page("person", "Nina Park", loops=[ASK]),
        LAKEVIEW: page("organization", "Lakeview Realty"),
    }


def test_a_page_with_an_open_loop_is_never_retired(data_root: Path, capsys):
    run_with_pages(data_root, pages_with_a_loop())
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    # Her page is emptied, and its loop goes with it; resumed, the session says the same.
    dropped = reporting(retiring(), dismissed("c02"))
    script(data_root, dreaming=[EMPTIED, dropped], dreaming_resume=[dropped])
    capsys.readouterr()

    assert dream() != 0

    attempts = sessions(data_root, "dreaming")
    assert ["--resume" in s["argv"] for s in attempts] == [False, True, True]
    told = f"c01 retires `nina-park`, but {NINA} held an open loop that is now on no page: `{ASK}`"
    assert told in attempts[1]["prompt"]
    assert "A page with an open loop is never retired" in attempts[1]["prompt"]
    error = capsys.readouterr().err
    assert told in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def test_a_page_whose_open_loop_moved_to_another_page_may_be_retired(data_root: Path):
    run_with_pages(data_root, pages_with_a_loop())
    # The loop is the agency's to carry: its page takes the line, still open.
    folded = page("organization", "Lakeview Realty", loops=[ASK])
    script(
        data_root,
        dreaming=[
            EMPTIED,
            {"write": LAKEVIEW, "text": folded},
            reporting(retiring(pages=[NINA, LAKEVIEW]), dismissed("c02")),
        ],
    )

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    files = wiki_files(data_root)
    assert NINA not in files
    assert files[LAKEVIEW] == folded
    assert files[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")


def gift() -> dict[str, bytes]:
    """One more email from David Kim, for the next nightly run to take."""
    return {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")}


LISTED = {"write": RETIRED, "text": retired_file(f"- nina-park · retired {TODAY}")}

# A session of each phase that writes the list itself, and how the run is started.
WRITERS = {
    "dreaming": ({"dreaming": [EMPTIED, LISTED, reporting(retiring())]}, dream),
    "consolidation": ({"consolidation": [LISTED]}, run_librarian),
}


@pytest.mark.parametrize("phase", WRITERS, ids=list(WRITERS))
def test_retired_md_is_the_hosts_and_a_session_that_writes_it_fails_the_run(
    data_root: Path, capsys, phase: str
):
    run_with_pages(data_root)
    ingest(data_root, gift())
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    phases, run = WRITERS[phase]
    script(data_root, **phases)
    capsys.readouterr()

    assert run() != 0

    assert f"the {phase} session changed retired.md, outside the pages" in capsys.readouterr().err
    # Out of bounds, the session isn't resumed.
    assert "--resume" not in sessions(data_root)[-1]["argv"]
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def commit_by_hand(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, so the cursor stays."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).parent.mkdir(parents=True, exist_ok=True)
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


SHIPPING = f"- shipping-updates · retired {days_ago(30)}"
"""A slug an earlier dreaming run retired."""
RETIREMENT = "expected `- <slug> · retired YYYY-MM-DD`"

# A `retired.md` the wiki was committed with, and what lint says of it.
RETIRED_LINT_ERRORS = {
    "a line that isn't a retirement": (
        retired_file(SHIPPING, "- parcel-post retired last week"),
        f"retired.md:4: malformed retirement: {RETIREMENT}, got '- parcel-post retired last week'",
    ),
    "a day that isn't one": (
        retired_file("- shipping-updates · retired 2026-02-30"),
        f"retired.md:3: malformed retirement: {RETIREMENT}",
    ),
    "a name that isn't a slug": (
        retired_file("- Shipping Updates · retired 2026-02-03"),
        f"retired.md:3: malformed retirement: {RETIREMENT}",
    ),
    "a link in place of a slug": (
        retired_file("- [[shipping-updates]] · retired 2026-02-03"),
        f"retired.md:3: malformed retirement: {RETIREMENT}",
    ),
    "a heading": (
        retired_file("## Senders", SHIPPING),
        f"retired.md:3: malformed retirement: {RETIREMENT}, got '## Senders'",
    ),
    "a slug listed twice": (
        retired_file(SHIPPING, f"- shipping-updates · retired {days_ago(2)}"),
        "retired.md:4: shipping-updates is already listed, on line 3",
    ),
}


@pytest.mark.parametrize("problem", RETIRED_LINT_ERRORS, ids=list(RETIRED_LINT_ERRORS))
def test_lint_checks_every_line_of_retired_md(data_root: Path, capsys, problem: str):
    run_with_pages(data_root)
    written, finding = RETIRED_LINT_ERRORS[problem]
    commit_by_hand(data_root, {RETIRED: written})
    ingest(data_root, gift())
    script(data_root)
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert "the wiki has 1 lint errors" in error
    assert finding in error
    # The host left the file for a person to put right.
    assert wiki_files(data_root)[RETIRED] == written


def test_a_wiki_carries_its_retired_slugs_through_the_runs_that_follow(data_root: Path):
    run_with_pages(data_root)
    # Written by an earlier run, without a title.
    commit_by_hand(data_root, {RETIRED: f"{SHIPPING}\n"})
    ingest(data_root, gift())
    script(data_root)

    assert run_librarian() == 0
    script(data_root, dreaming=[EMPTIED, reporting(retiring())])
    assert dream() == 0

    assert wiki_files(data_root)[RETIRED] == f"{SHIPPING}\n- nina-park · retired {TODAY}\n"
