"""Retirement: dreaming clears the pages the owner has no use for, and they stay cleared (#147).

Whole runs of `pa librarian dream` and `pa librarian run` over a temporary data root, through
the stand-in `claude` (the conftest at the repository root says what it does and how it's
scripted). Each test builds a wiki with a librarian run (`run_with_pages`), scripts the sessions'
writes and the outcomes a dreaming session reports, and asserts on the wiki, its git history,
and what the sessions were given.

The wiki is David Kim's page and his wedding's, which the owner has written about, and a page
for Nina Park, a realtor who wrote to the owner once and never heard back.
"""

import json
import os
import re
import subprocess
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.librarian.dreaming import Applied, Outcomes
from pa_home.cli import main
from pa_home.git_wiki import GitWikiRepository
from pa_home.librarian_project import LIBRARIAN_PROJECT

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
    # These wikis link their pages on days after the pages' last update, which makes each one a
    # stale candidate ahead of the unengaged one under test. Three later days keeps them out.
    monkeypatch.setenv("PA_LIBRARIAN_STALE_LATER_DAYS", "3")
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
    """One librarian run over mail from David Kim, the owner's reply to him, and mail from Nina
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

    error = run_error(data_root)
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


def test_a_retired_page_its_outcome_leaves_out_of_its_pages_resumes_the_session(
    data_root: Path,
):
    run_with_pages(data_root, {**pages(), LAKEVIEW: page("organization", "Lakeview Realty")})
    # What her page held goes to her agency's, which the outcome names; her own page it names
    # only as retired.
    folded = page(
        "organization", "Lakeview Realty", notes=[f"Its agent sent a listing [ep:{HOTEL}]."]
    )
    script(
        data_root,
        dreaming=[
            EMPTIED,
            {"write": LAKEVIEW, "text": folded},
            reporting(retiring(pages=[LAKEVIEW]), dismissed("c02")),
        ],
        dreaming_resume=[reporting(retiring(pages=[NINA, LAKEVIEW]), dismissed("c02"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert (
        f"c01 retires `nina-park`, but doesn't name {NINA} among its `pages`" in (resumed["prompt"])
    )
    files = wiki_files(data_root)
    assert NINA not in files
    assert files[LAKEVIEW] == folded
    assert files[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")
    listed = commit_messages(data_root)[0].split("\n\n")[1].splitlines()
    assert listed[1] == f"- c01 · unengaged · {NINA} · applied: {NINA}, {LAKEVIEW}"


ASK = f"- [ ] me → [[nina-park]]: a reply about the listing · asked {days_ago(1)} [ep:{HOTEL}]"
"""An open loop on Nina's page."""


def pages_with_a_loop(loop: str = ASK) -> dict[str, str]:
    """The pages, with an open loop on Nina's and a page for her agency."""
    return {
        **pages(),
        NINA: page("person", "Nina Park", loops=[loop]),
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
    error = run_error(data_root)
    assert told in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


CALL = f"- [ ] me → [[lakeview-realty]]: a call back · promised {days_ago(1)} [ep:{HOTEL}]"
"""An open loop on Nina's page that is her agency's: it doesn't name her."""


def test_a_page_whose_open_loop_moved_to_another_page_may_be_retired(data_root: Path):
    run_with_pages(data_root, pages_with_a_loop(CALL))
    # The loop is the agency's to carry: its page takes the line, still open.
    folded = page("organization", "Lakeview Realty", loops=[CALL])
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


def test_a_page_whose_own_loop_names_it_isnt_retired_by_moving_the_loop(data_root: Path):
    written = pages_with_a_loop()
    run_with_pages(data_root, written)
    # The reply the owner owes her moves to her agency's page, and still names her.
    moved = {"write": LAKEVIEW, "text": page("organization", "Lakeview Realty", loops=[ASK])}
    script(
        data_root,
        dreaming=[
            EMPTIED,
            moved,
            reporting(retiring(pages=[NINA, LAKEVIEW]), dismissed("c02")),
        ],
        dreaming_resume=[
            *({"write": path, "text": written[path]} for path in (NINA, LAKEVIEW)),
            reporting(dismissed(), dismissed("c02")),
        ],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    told = f"c01 retires `nina-park`, but an open loop on {LAKEVIEW} names it as a party"
    assert told in resumed["prompt"]
    files = wiki_files(data_root)
    assert {path: files[path] for path in written} == written
    assert RETIRED not in files


LISTING = "topics/listing.md"
LISTING_LINKS = {**LINKS, "Listing": ["nina-park", "lakeview-realty", "listing"]}
"""The links of a wiki whose observations give Nina's mail a topic of its own."""
PRICE_SHEET = f"- [ ] [[nina-park]] → me: the price sheet · asked {days_ago(1)} [ep:{HOTEL}]"
"""An open loop on the listing's page that names Nina as the party owing."""


def listing_page(*, aliases: str = "[]", notes: Sequence[str] = ()) -> str:
    """The listing's page, which holds the loop Nina owes."""
    return page("topic", "Listing", aliases=aliases, loops=[PRICE_SHEET], notes=notes)


def pages_with_a_listing() -> dict[str, str]:
    """The pages, with a topic for the listing Nina wrote about."""
    return {**pages(), LISTING: listing_page()}


def test_a_page_another_pages_open_loop_names_as_a_party_is_never_retired(data_root: Path):
    run_with_pages(data_root, pages_with_a_listing(), LISTING_LINKS)
    before = wiki_files(data_root)
    # Her own page holds no loop, and the listing's holds one she owes. Resumed, the session
    # writes her page back and dismisses the candidate.
    script(
        data_root,
        dreaming=[EMPTIED, reporting(retiring())],
        dreaming_resume=[{"write": NINA, "text": pages()[NINA]}, reporting(dismissed())],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert (
        f"c01 retires `nina-park`, but an open loop on {LISTING} names it as a party. A loop's "
        f"party is never retired: write {NINA} back as it was and propose or dismiss the "
        "candidate"
    ) in resumed["prompt"]
    assert wiki_files(data_root) == before
    assert RETIRED not in before


MERGED_LISTING = listing_page(
    aliases="[Nina Park]", notes=[f"Nina Park sends the listing's mail [ep:{HOTEL}]."]
)
"""The listing's page as a session leaves it that merges Nina's page into it: her name among
its aliases, and a line saying who she is."""
MERGED_AWAY = [EMPTIED, {"write": LISTING, "text": MERGED_LISTING}]
MERGING = {"candidate": "c01", "outcome": "applied", "pages": [NINA, LISTING]}
NOT_A_DUPLICATE = (
    f"c01 merges {NINA} into {LISTING}, but isn't a `duplicate` candidate that names both pages"
)


def test_a_page_merged_away_under_a_candidate_that_isnt_a_duplicate_resumes_the_session(
    data_root: Path,
):
    run_with_pages(data_root, pages_with_a_listing(), LISTING_LINKS)
    before = wiki_files(data_root)
    # Her page is flagged as unengaged, and the session folds it into the listing's as if it
    # were a duplicate. Resumed, it writes both pages back and dismisses the candidate.
    script(
        data_root,
        dreaming=[*MERGED_AWAY, reporting(MERGING)],
        dreaming_resume=[
            {"write": NINA, "text": pages()[NINA]},
            {"write": LISTING, "text": listing_page()},
            reporting(dismissed()),
        ],
    )

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert [(c["id"], c["kind"], c["pages"]) for c in started["candidates"]] == [
        ("c01", "unengaged", [NINA])
    ]
    assert NOT_A_DUPLICATE in resumed["prompt"]
    assert (
        f"Write {NINA} back as it was, take its name off {LISTING}'s aliases, and propose or "
        "dismiss c01"
    ) in resumed["prompt"]
    # The page, the topic's aliases, and the loop are as they were.
    assert wiki_files(data_root) == before
    listed = commit_messages(data_root)[0].split("\n\n")[1].splitlines()
    assert listed == ["Candidates:", f"- c01 · unengaged · {NINA} · dismissed"]


def test_a_merge_taken_back_only_by_its_alias_leaves_the_emptied_page_to_fail(
    data_root: Path, capsys
):
    run_with_pages(data_root, pages_with_a_listing(), LISTING_LINKS)
    before = tree_state(data_root / "wiki")
    # Resumed, the session takes her name off the listing's aliases, never writes her page
    # back, and claims it as changed. The host has put the page back where the session emptied
    # it, so it is an emptied page again, and never a page that is simply gone.
    claimed = reporting({"candidate": "c01", "outcome": "applied", "pages": [NINA]})
    script(
        data_root,
        dreaming=[*MERGED_AWAY, reporting(MERGING)],
        dreaming_resume=[{"write": LISTING, "text": listing_page()}, claimed],
    )
    capsys.readouterr()

    assert dream() != 0

    attempts = sessions(data_root, "dreaming")
    assert NOT_A_DUPLICATE in attempts[1]["prompt"]
    assert f"{NINA}:1: bad frontmatter" in attempts[2]["prompt"]
    error = run_error(data_root)
    assert f"{NINA}:1: bad frontmatter" in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before


def test_a_page_merged_away_that_no_outcome_names_resumes_the_session(data_root: Path):
    run_with_pages(data_root, pages_with_a_listing(), LISTING_LINKS)
    before = wiki_files(data_root)
    script(
        data_root,
        dreaming=[*MERGED_AWAY, reporting(dismissed())],
        dreaming_resume=[
            {"write": NINA, "text": pages()[NINA]},
            {"write": LISTING, "text": listing_page()},
            reporting(dismissed()),
        ],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert (
        f"{NINA} was merged into {LISTING} (it is emptied, and its name is among that page's "
        "aliases), under no `duplicate` candidate that names both pages"
    ) in resumed["prompt"]
    assert wiki_files(data_root) == before


def pages_that_may_be_one() -> dict[str, str]:
    """The pages, with Nina's name among the aliases of the listing's page, so the two are
    flagged as a duplicate, and a page for her agency."""
    return {
        **pages(),
        LISTING: listing_page(aliases="[Nina Park]"),
        LAKEVIEW: page("organization", "Lakeview Realty"),
    }


MAY_BE_ONE = [("duplicate", [LISTING, NINA]), ("unengaged", [NINA]), ("unengaged", [LAKEVIEW])]
"""The candidates of `pages_that_may_be_one`, c01 to c03, by kind and pages."""
KEPT = listing_page(
    aliases="[Nina Park]", notes=[f"Nina Park is the listing's own mailbox [ep:{HOTEL}]."]
)
"""The listing's page once it has taken Nina's in."""


def test_a_page_merged_away_under_a_duplicate_candidate_naming_both_pages_is_accepted(
    data_root: Path,
):
    run_with_pages(data_root, pages_that_may_be_one(), LISTING_LINKS)
    script(
        data_root,
        dreaming=[
            EMPTIED,
            {"write": LISTING, "text": KEPT},
            reporting(MERGING, dismissed("c02"), dismissed("c03")),
        ],
    )

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert [(c["kind"], c["pages"]) for c in session["candidates"]] == MAY_BE_ONE
    files = wiki_files(data_root)
    assert NINA not in files
    # The host rewrote the loop's link to her as a link to the page that took her name.
    assert files[LISTING] == KEPT.replace("[[nina-park]]", "[[listing|nina-park]]")


# How a session merges Nina's page away in a wiki where the listing's page is flagged as her
# duplicate, the outcomes it reports, and the merge the host names.
NOT_ITS_DUPLICATE = {
    "under the page's unengaged candidate": (
        [EMPTIED, {"write": LISTING, "text": KEPT}],
        [dismissed("c01"), {**MERGING, "candidate": "c02"}, dismissed("c03")],
        f"c02 merges {NINA} into {LISTING}",
    ),
    "into a page its duplicate candidate doesn't name": (
        [
            EMPTIED,
            {"write": LISTING, "text": listing_page()},
            {
                "write": LAKEVIEW,
                "text": page("organization", "Lakeview Realty", aliases="[Nina Park]"),
            },
        ],
        [{**MERGING, "pages": [NINA, LISTING, LAKEVIEW]}, dismissed("c02"), dismissed("c03")],
        f"c01 merges {NINA} into {LAKEVIEW}",
    ),
}


@pytest.mark.parametrize("merge", NOT_ITS_DUPLICATE, ids=list(NOT_ITS_DUPLICATE))
def test_a_merge_no_duplicate_candidate_naming_both_pages_settles_resumes_the_session(
    data_root: Path, merge: str
):
    written = pages_that_may_be_one()
    run_with_pages(data_root, written, LISTING_LINKS)
    actions, outcomes, told = NOT_ITS_DUPLICATE[merge]
    script(
        data_root,
        dreaming=[*actions, reporting(*outcomes)],
        dreaming_resume=[
            *({"write": path, "text": text} for path, text in written.items()),
            reporting(dismissed("c01"), dismissed("c02"), dismissed("c03")),
        ],
    )

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert [(c["kind"], c["pages"]) for c in started["candidates"]] == MAY_BE_ONE
    assert f"{told}, but isn't a `duplicate` candidate that names both pages" in resumed["prompt"]
    files = wiki_files(data_root)
    assert {path: files[path] for path in written} == written


def gift() -> dict[str, bytes]:
    """One more email from David Kim, sent yesterday, for the next librarian run to take."""
    sent_yesterday = mailed(1, "08:00")
    return {
        "gmail/gift.eml": email(
            message_id="wedding-0003@example.net", date=sent_yesterday, subject="Gift"
        )
    }


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

    assert f"the {phase} session changed retired.md, outside the pages" in run_error(data_root)
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
    repository.commit(repository.commits()[0].message)


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

    error = run_error(data_root)
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


def reference_counts(session: dict) -> dict[str, dict]:
    """The reference counts a consolidation session was given, by slug: its prompt's second
    JSON block."""
    return {count["slug"]: count for count in session["inputs"][1]}


def test_the_next_librarian_run_is_told_a_retired_slug_is_retired_and_not_at_the_threshold(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_PAGE_THRESHOLD", "2")
    # Nina wrote twice, so she and her agency are linked on two days: at the threshold, where
    # an organization gets a page whether or not the owner has engaged with it.
    ingest(data_root, {"gmail/listing-2.eml": listing(9, "listing-0002@lakeview.example")})
    run_with_pages(data_root, {**pages(), LAKEVIEW: page("organization", "Lakeview Realty")})
    [first] = sessions(data_root, "consolidation")
    assert reference_counts(first)["lakeview-realty"] == {
        "slug": "lakeview-realty",
        "days": 2,
        "reaches_threshold": True,
        "engaged": False,
    }
    script(
        data_root,
        dreaming=[
            EMPTIED,
            {"write": LAKEVIEW, "text": ""},
            reporting(retiring(), retiring("lakeview-realty", pages=[LAKEVIEW], candidate="c02")),
        ],
    )
    assert dream() == 0
    listed = retired_file(f"- lakeview-realty · retired {TODAY}", f"- nina-park · retired {TODAY}")
    assert wiki_files(data_root)[RETIRED] == listed
    ingest(data_root, gift())
    script(data_root)

    assert run_librarian() == 0

    session = sessions(data_root, "consolidation")[-1]
    counts = reference_counts(session)
    for slug in ("lakeview-realty", "nina-park"):
        assert counts[slug] == {
            "slug": slug,
            "days": 2,
            "reaches_threshold": False,
            "engaged": False,
            "retired": True,
        }
    # No other slug carries the flag.
    assert "retired" not in counts["david-kim"]
    assert "`retired`" in session["prompt"]
    # The run made no page for either, and left the list as it was.
    files = wiki_files(data_root)
    assert NINA not in files and LAKEVIEW not in files
    assert files[RETIRED] == listed


def test_lint_gives_no_slug_without_page_warning_for_a_retired_slug(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_PAGE_THRESHOLD", "2")
    run_with_pages(data_root)
    # The owner has written to David Kim, and he is linked on two days: without a page he
    # would be a warning. Whatever the candidate, the host retires what its outcome names.
    script(
        data_root,
        dreaming=[{"write": DAVID, "text": ""}, reporting(retiring("david-kim", pages=[DAVID]))],
    )

    assert dream() == 0

    assert fields(commit_messages(data_root)[0])["Lint-Warnings"] == "0"
    # Another email from him doesn't bring the warning back: he wrote, the owner didn't.
    ingest(data_root, gift())
    script(data_root, observation=[{"observe": True, "links": {"Gift": ["david-kim"]}}])
    assert run_librarian() == 0
    session = sessions(data_root, "consolidation")[-1]
    assert session["warnings"] == []
    assert reference_counts(session)["david-kim"] == {
        "slug": "david-kim",
        "days": 3,
        "reaches_threshold": False,
        "engaged": True,
        "retired": True,
    }
    assert fields(commit_messages(data_root)[0])["Lint-Warnings"] == "0"


PROPOSALS = "proposals.md"
ASKED = (
    "- [ ] P1 · question · Does [[nina-park]] still speak for [[lakeview-realty]]? · "
    f"proposed {TODAY} [ep:{HOTEL}]"
)
"""An open proposal that links Nina's slug."""


def proposals_file(*opened: str) -> str:
    return "\n".join(["# Proposals", "", "## Open", *opened, "", "## Answered", ""])


def test_a_link_to_a_retired_slug_lints_clean(data_root: Path):
    # Her agency's page links her, as the day logs do.
    agency = page(
        "organization", "Lakeview Realty", notes=[f"[[nina-park]] is an agent here [ep:{HOTEL}]."]
    )
    run_with_pages(data_root, {**pages(), LAKEVIEW: agency})
    # One run retires her page and asks the owner about her agency, linking her.
    script(
        data_root,
        dreaming=[
            EMPTIED,
            {"write": PROPOSALS, "text": proposals_file(ASKED)},
            reporting(retiring(), {"candidate": "c02", "outcome": "proposed", "proposal": "P1"}),
        ],
    )

    assert dream() == 0

    # The proposal's link was good as the session left it: nothing was resumed.
    assert len(sessions(data_root, "dreaming")) == 1
    files = wiki_files(data_root)
    assert files[PROPOSALS] == proposals_file(ASKED)
    assert files[LAKEVIEW] == agency
    assert files[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")
    assert fields(commit_messages(data_root)[0])["Lint-Warnings"] == "0"
    # The next librarian run lints the same wiki, and commits.
    ingest(data_root, gift())
    script(data_root)
    assert run_librarian() == 0
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(ASKED)
    assert fields(commit_messages(data_root)[0])["Lint-Warnings"] == "0"


def test_an_open_proposal_already_linking_a_page_doesnt_stop_its_retirement(data_root: Path):
    run_with_pages(data_root)
    # An earlier run asked about her, and the owner hasn't answered.
    asked = (
        "- [ ] P1 · question · Is [[nina-park]] selling the house next door? · "
        f"proposed {days_ago(8)} [ep:{HOTEL}]"
    )
    commit_by_hand(data_root, {PROPOSALS: proposals_file(asked)})
    script(data_root, dreaming=[EMPTIED, reporting(retiring())])

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    files = wiki_files(data_root)
    assert NINA not in files
    assert files[PROPOSALS] == proposals_file(asked)


def test_a_retirement_a_resumed_session_no_longer_reports_leaves_its_emptied_page_to_fail(
    data_root: Path, capsys
):
    run_with_pages(data_root, {**pages(), LAKEVIEW: page("organization", "Lakeview Realty")})
    before = tree_state(data_root / "wiki")
    # First her page is retired, with no outcome for the agency's. Resumed, the session
    # reports hers as changed and nothing more.
    claimed = {"candidate": "c01", "outcome": "applied", "pages": [NINA]}
    script(
        data_root,
        dreaming=[EMPTIED, reporting(retiring())],
        dreaming_resume=[reporting(claimed, dismissed("c02"))],
    )
    capsys.readouterr()

    assert dream() != 0

    error = run_error(data_root)
    assert f"{NINA}:1: bad frontmatter" in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before


def test_the_hosts_own_writes_to_retired_md_are_never_blamed_on_a_resumed_session(
    data_root: Path, capsys
):
    run_with_pages(data_root, {**pages(), LAKEVIEW: page("organization", "Lakeview Realty")})
    # Her page is retired, with no outcome for the agency's, so the host has listed her when
    # it resumes the session. Resumed, the session reports nothing, twice.
    script(data_root, dreaming=[EMPTIED, reporting(retiring())])
    capsys.readouterr()

    assert dream() != 0

    assert len(sessions(data_root, "dreaming")) == 3
    error = run_error(data_root)
    assert "holds no outcomes" in error
    assert "retired.md" not in error


def retire_nina_at_the_threshold(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Builds the wiki with Nina linked on two days, the page threshold, and retires her
    page."""
    monkeypatch.setenv("PA_LIBRARIAN_PAGE_THRESHOLD", "2")
    ingest(data_root, {"gmail/listing-2.eml": listing(9, "listing-0002@lakeview.example")})
    run_with_pages(data_root)
    script(data_root, dreaming=[EMPTIED, reporting(retiring())])
    assert dream() == 0
    assert wiki_files(data_root)[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")


def viewing(days: int) -> dict[str, bytes]:
    """The owner's reply to Nina, sent `days` days ago."""
    to = "Nina Park <nina@lakeview.example>"
    return {"gmail/viewing.eml": sent("Viewing", days, "listing-0003@example.com", to=to)}


def test_a_retired_slug_the_owner_then_engages_with_is_at_the_threshold_again(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    retire_nina_at_the_threshold(data_root, monkeypatch)
    # The owner writes back to her later on the day her page was retired: that counts.
    ingest(data_root, viewing(0))
    script(data_root, observation=[{"observe": True, "links": {"Viewing": ["nina-park"]}}])

    assert run_librarian() == 0

    session = sessions(data_root, "consolidation")[-1]
    assert reference_counts(session)["nina-park"] == {
        "slug": "nina-park",
        "days": 3,
        "reaches_threshold": True,
        "engaged": True,
        "retired": True,
    }
    # This consolidation wrote her no page, so lint warns again, and she stays on the list.
    recorded = commit_messages(data_root)[0]
    assert "`nina-park` is linked on 3 days, reaching the page threshold of 2" in recorded
    assert wiki_files(data_root)[RETIRED] == retired_file(f"- nina-park · retired {TODAY}")


# What the night after a retirement brings, and what its observation links: each ends with
# consolidation writing Nina a page again.
RETURNS = {
    # At the threshold again, by the reference counts.
    "the owner engages with the slug": (viewing(0), {"Viewing": ["nina-park"]}),
    # Below it still: the owner asked for the page, or a loop names her.
    "consolidation creates the page for another reason": (gift(), {"Gift": ["david-kim"]}),
}


@pytest.mark.parametrize("reason", RETURNS, ids=list(RETURNS))
def test_once_a_retired_slug_has_a_page_again_the_host_drops_it_from_retired_md(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, reason: str
):
    retire_nina_at_the_threshold(data_root, monkeypatch)
    mail, links = RETURNS[reason]
    ingest(data_root, mail)
    script(
        data_root,
        observation=[{"observe": True, "links": links}],
        consolidation=[{"write": NINA, "text": pages()[NINA]}],
    )

    assert run_librarian() == 0

    files = wiki_files(data_root)
    assert files[NINA] == pages()[NINA]
    # She was the only one retired, so the list goes too, in the run's own commit.
    assert RETIRED not in files
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert {NINA, RETIRED} <= set(changed)
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    # The next consolidation is told nothing of a retirement.
    ingest(data_root, {"gmail/menu.eml": email(message_id="wedding-0009@example.net")})
    script(data_root)
    assert run_librarian() == 0
    assert "retired" not in reference_counts(sessions(data_root, "consolidation")[-1])["nina-park"]


def test_a_dreaming_run_that_gives_a_retired_slug_its_page_back_drops_it_from_retired_md(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    retire_nina_at_the_threshold(data_root, monkeypatch)
    # Another slug was retired a month ago, and stays so.
    commit_by_hand(data_root, {RETIRED: retired_file(SHIPPING, f"- nina-park · retired {TODAY}")})
    ingest(data_root, viewing(0))
    script(data_root, observation=[{"observe": True, "links": {"Viewing": ["nina-park"]}}])
    assert run_librarian() == 0
    # Engaged and at the threshold with no page, her slug is a candidate, and dreaming
    # creates the page.
    created = {"candidate": "c01", "outcome": "applied", "pages": [NINA]}
    script(data_root, dreaming=[{"write": NINA, "text": pages()[NINA]}, reporting(created)])

    assert dream() == 0

    [candidate] = sessions(data_root, "dreaming")[-1]["candidates"]
    assert (candidate["kind"], candidate["slug"]) == ("slug-without-page", "nina-park")
    files = wiki_files(data_root)
    assert files[NINA] == pages()[NINA]
    assert files[RETIRED] == retired_file(SHIPPING)
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert {NINA, RETIRED} <= set(changed)


@pytest.mark.parametrize("name", ["index", "proposals", "retired"])
def test_a_page_cant_take_the_name_of_a_file_at_the_wikis_top_level(
    data_root: Path, capsys, name: str
):
    run_with_pages(data_root)
    ingest(data_root, gift())
    # A topic the mail calls by that name: `[[proposals]]` would no longer mean one file.
    named = {"write": f"topics/{name}.md", "text": page("topic", name.capitalize())}
    script(data_root, consolidation=[named], consolidation_resume=[])
    capsys.readouterr()

    assert run_librarian() != 0

    error = run_error(data_root)
    assert (
        f"topics/{name}.md: a page can't be named `{name}`: {name}.md is a file at the wiki's "
        f"top level, and [[{name}]] links it"
    ) in error
    assert "the consolidation session left 1 lint errors after 2 resumes" in error


def test_a_dreaming_session_is_given_the_retired_slugs(data_root: Path):
    run_with_pages(data_root)
    commit_by_hand(data_root, {RETIRED: retired_file(SHIPPING)})
    script(data_root, dreaming=[reporting(dismissed())])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert "\n## Retired slugs\n" in session["prompt"]
    # The section's JSON block is the prompt's last.
    assert session["inputs"][-1] == [{"slug": "shipping-updates", "retired": days_ago(30)}]
    assert f"`{data_root / 'wiki' / RETIRED}`" in session["prompt"]


def test_a_dreaming_session_over_a_wiki_with_nothing_retired_is_told_so(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=[reporting(dismissed())])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert "\n## Retired slugs\n" in session["prompt"]
    assert session["inputs"][-1] == []


def instructions_text(section: str) -> str:
    """A `## ` section of the librarian's instructions, as the checkout has them (a session
    runs the project as committed)."""
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    found = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", claude_md, re.M | re.S)
    assert found is not None, f"CLAUDE.md has no {section} section"
    return found[1]


def instructions(section: str, subsection: str | None = None) -> str:
    """A `## ` section of the librarian's instructions, or one `### ` subsection of it, with
    its whitespace collapsed."""
    text = instructions_text(section)
    if subsection is not None:
        found = re.search(rf"^### {subsection}\n(.*?)(?=^### |\Z)", text, re.M | re.S)
        assert found is not None, f"{section} has no {subsection} subsection"
        text = found[1]
    return " ".join(text.split())


def judging(kind: str) -> str:
    """How the instructions say to judge a `kind` candidate: its bullet, whitespace collapsed."""
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    bullet = re.search(rf"^- \*\*`{kind}`\*\*.*?(?=^- \*\*`|^### )", claude_md, re.M | re.S)
    assert bullet is not None, f"no bullet says how to judge a {kind} candidate"
    return " ".join(bullet[0].split())


def test_the_dreaming_instructions_cover_the_three_judgments_of_an_unengaged_page():
    unengaged = judging("unengaged")

    # No open loop, and nothing owed either way: retire it, folding what is worth keeping.
    assert "nothing owed either way" in unengaged
    assert "retire it" in unengaged
    assert "into the page it belongs" in unengaged
    # An organization the owner plainly relies on is kept.
    assert "a bank" in unengaged and "a school" in unengaged
    assert "dismiss it" in unengaged
    # An open loop: propose, never retire.
    assert "open loop" in unengaged
    assert "never retire" in unengaged
    assert "propose" in unengaged


def test_the_dreaming_instructions_say_an_orphan_may_be_retired_and_how_to_retire_a_page():
    assert "retire it" in judging("orphan")
    retiring_a_page = instructions("Dreaming", "Retiring a page")
    # The session empties the page and names its slug; the host does the rest.
    assert "with no content at all" in retiring_a_page
    assert "`retired`" in retiring_a_page
    assert "Never write `retired.md`" in retiring_a_page
    # The day logs aren't rewritten, and links stay.
    assert "Leave every link to it" in retiring_a_page


def test_the_dreaming_instructions_say_a_merge_settles_only_a_duplicate():
    # Under the kind whose fix it is, and where retiring is told apart from it.
    assert "Merging belongs to this kind alone" in judging("duplicate")
    retiring_a_page = instructions("Dreaming", "Retiring a page")
    assert "a merge, which settles only a `duplicate` candidate" in retiring_a_page


def test_the_dreaming_instructions_say_a_loops_party_stays():
    retiring_a_page = instructions("Dreaming", "Retiring a page")

    assert "A page that an open loop on any page names as a party stays" in retiring_a_page
    assert "an automated sender's page included" in retiring_a_page
    # Its candidate is still judged, without retiring the page.
    assert "otherwise propose or dismiss" in retiring_a_page


def test_the_dreaming_instructions_say_a_fold_goes_line_by_line():
    retiring_a_page = instructions("Dreaming", "Retiring a page")

    # Each line of the page about to be emptied is held against the page it folds into.
    assert "check each of its lines against the page it folds into" in retiring_a_page
    # A kept line citing the same episodes may say less, or something else.
    assert "Carry over whatever the kept page's line on the same episodes lacks" in retiring_a_page
    assert "correct the kept line from the episodes" in retiring_a_page
    # Done is every line accounted for: on the kept page as the episodes give it, or of no use.
    assert "with its detail and its date as the episodes give them" in retiring_a_page
    assert "or is one the owner has no use for" in retiring_a_page


def test_the_instructions_say_a_dreaming_finding_is_fixed_with_every_page_still_there():
    findings = instructions("Lint findings")

    # Dreaming is resumed only for what it caused, and the rest is consolidation's.
    assert "only with findings your own edits caused" in findings
    assert "waits for the next consolidation" in findings
    # A finding is no reason to merge or retire.
    assert "Fix each finding in place" in findings
    assert "every page still there" in findings


def test_the_instructions_example_outcomes_show_a_retirement():
    [example] = re.findall(r"^```json\n(.*?)^```$", instructions_text("Dreaming"), re.M | re.S)

    outcomes = Outcomes.model_validate_json(example).outcomes

    retired = [o.retired for o in outcomes if isinstance(o, Applied) and o.retired]
    assert retired == [("shipping-updates",)]
    assert "`retired`" in instructions("Dreaming", "Outcomes")


def test_the_consolidation_instructions_cover_a_retired_slug_and_bringing_its_page_back():
    consolidation = instructions("Consolidation")
    # A retired slug stays without a page until the reference counts say otherwise.
    assert "marked `retired`" in consolidation
    assert "never write `retired.md`" in consolidation
    # The owner's word brings a page back, whatever the counts say.
    corrections = instructions("Corrections")
    assert "keep a page for" in corrections
    assert "retired" in corrections
    assert "the host then drops its slug from `retired.md`" in corrections


def test_the_glossary_defines_a_retired_page():
    glossary = (LIBRARIAN_PROJECT.parent / "GLOSSARY.md").read_text()
    entry = re.search(r"^\*\*Retired page\*\*:\n(.*?)\n_Avoid_: ", glossary, re.M | re.S)

    assert entry is not None
    for said in ("never engaged with", "`retired.md`", "The day logs keep its facts"):
        assert said in entry[1], said
