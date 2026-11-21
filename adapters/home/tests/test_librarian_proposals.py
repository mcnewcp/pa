"""Proposals: what dreaming asks the owner, in `proposals.md` at the wiki's root (#143).

Whole runs of `pa librarian dream` and `pa librarian run` over a temporary data root, through
the stand-in `claude` (the conftest at the repository root says what it does and how it's
scripted). Each test builds a wiki with a nightly run, scripts the dreaming session's writes
and the outcomes its final message reports, and asserts on the wiki, its git history, and what
the session was given: the stand-in records the open proposals listed under the prompt's
`## Open proposals` heading as `proposals`.
"""

import json
import os
import re
import subprocess
import textwrap
from datetime import datetime, time, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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
# and one nothing has linked for 90 days is a candidate.
ROOM_LIST_DAY = days_ago(10)
"""The day the later email of `run_with_pages` is logged on, and its pages were updated."""
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


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


TODAY = days_ago(0)
DAVID, DAVE, TOPIC = "people/david-kim.md", "people/dave.md", "topics/wedding.md"
PROPOSALS = "proposals.md"


def loop(what: str = "the room list", opened: int = 1) -> str:
    """An open loop on the wedding's page, opened `opened` days ago."""
    return f"- [ ] [[david-kim]] → me: {what} · asked {days_ago(opened)} [ep:{HOTEL}]"


def pages(*loops: str) -> dict[str, str]:
    """David Kim's page, a second page for him under the name his own has as an alias (lint's
    duplicate candidate), and his wedding's page with `loops` (one opened yesterday unless
    given). Every page is linked from a day log."""
    return {
        DAVID: textwrap.dedent(
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
        DAVE: textwrap.dedent(
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
        + "\n".join(loops or (loop(),))
        + "\n\n## Notes\n",
    }


LINKS = {"Hotel block": ["david-kim", "wedding"], "Room list": ["dave", "wedding"]}
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


def proposal(
    number: int = 1,
    text: str = "Are [[dave]] and [[david-kim]] the same person?",
    *,
    kind: str = "change",
    cites: str = f"[ep:{HOTEL}]",
) -> str:
    """An open proposal's line, proposed today."""
    return f"- [ ] P{number} · {kind} · {text} · proposed {TODAY} {cites}"


def proposals_file(*opened: str, answered: tuple[str, ...] = ()) -> str:
    """`proposals.md` holding the `opened` lines under Open and the `answered` ones under
    Answered."""
    return "\n".join(["# Proposals", "", "## Open", *opened, "", "## Answered", *answered, ""])


def proposing(*opened: str, answered: tuple[str, ...] = ()) -> dict:
    """The stand-in's action writing `proposals.md` with those lines."""
    return {"write": PROPOSALS, "text": proposals_file(*opened, answered=answered)}


def reporting(*outcomes: dict) -> dict:
    """The stand-in's action ending a session with `outcomes` in its final message."""
    return {"outcomes": list(outcomes)}


def proposed(proposal_id: str = "P1", candidate: str = "c01") -> dict:
    return {"candidate": candidate, "outcome": "proposed", "proposal": proposal_id}


DISMISSED = {"candidate": "c01", "outcome": "dismissed"}


def test_a_dreaming_run_that_proposes_commits_the_proposal_and_its_outcome_names_it(
    data_root: Path, capsys
):
    run_with_pages(data_root)
    history = commit_messages(data_root)
    script(data_root, dreaming=[proposing(proposal()), reporting(proposed("P1"))])
    capsys.readouterr()

    assert dream() == 0

    # The duplicate L0 can't settle was the run's one candidate.
    [session] = sessions(data_root, "dreaming")
    assert [(c["id"], c["kind"], c["pages"]) for c in session["candidates"]] == [
        ("c01", "duplicate", [DAVID, DAVE])
    ]
    # One commit, holding the proposal as the session wrote it; no page changed.
    assert commit_messages(data_root)[1:] == history
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert PROPOSALS in changed
    assert not [path for path in changed if "/" in path]
    # The commit names the proposal on its candidate's line, and counts it.
    summary, listed, last = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 proposed, 0 dismissed"
    assert listed.splitlines() == [
        "Candidates:",
        f"- c01 · duplicate · {DAVID}, {DAVE} · proposed: P1",
    ]
    recorded = fields(last)
    assert recorded["Dreaming-Proposed"] == "1"
    assert (recorded["Dreaming-Applied"], recorded["Dreaming-Dismissed"]) == ("0", "0")
    assert "judged 1 candidates (0 applied, 1 proposed, 0 dismissed)" in capsys.readouterr().out
    # The next nightly run commits over the proposal, and leaves it as it is.
    ingest(
        data_root,
        {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")},
    )
    script(data_root)
    assert run_librarian() == 0
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())


def test_a_proposed_outcome_naming_a_proposal_that_isnt_in_the_file_fails_the_run(
    data_root: Path, capsys
):
    run_with_pages(data_root)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    # The session says it proposed, and wrote nothing; resumed, it says so again.
    script(data_root, dreaming=[reporting(proposed("P1"))], dreaming_resume=[reporting(proposed())])
    capsys.readouterr()

    assert dream() != 0

    attempts = sessions(data_root, "dreaming")
    assert ["--resume" in s["argv"] for s in attempts] == [False, True, True]
    told = "c01 names P1, which isn't an open proposal in proposals.md"
    assert told in attempts[1]["prompt"]
    error = capsys.readouterr().err
    assert told in error
    assert "The dreaming run was discarded: the wiki is as last committed." in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def test_a_session_resumed_for_a_proposal_it_named_but_never_wrote_can_write_it(data_root: Path):
    run_with_pages(data_root)
    script(
        data_root,
        dreaming=[proposing(proposal(1)), reporting(proposed("P2"))],
        dreaming_resume=[reporting(proposed("P1"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert "c01 names P2, which isn't an open proposal in proposals.md" in resumed["prompt"]
    assert "end your reply with the outcomes again" in resumed["prompt"]
    summary = commit_messages(data_root)[0].splitlines()[0]
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 proposed, 0 dismissed"
    assert fields(commit_messages(data_root)[0])["Dreaming-Resumes"] == "1"


QUESTION = "Are [[dave]] and [[david-kim]] the same person?"


def padded(length: int) -> str:
    """The question, padded to `length` characters."""
    return QUESTION + " " + "x" * (length - len(QUESTION) - 1)


ANSWERED_LINE = (
    f"- [x] P2 · question · Did [[david-kim]] send the room list? · proposed {days_ago(9)} "
    f"· answered {days_ago(2)} [ep:{HOTEL}] [ep:{HOTEL}]"
)
"""An answered proposal in the schema's format (whether its last citation is the owner's answer
is a later rule)."""

# What a dreaming session leaves in `proposals.md` beside its outcome, proposed P1, and what
# lint says of it when the session is resumed.
LINT_ERRORS = {
    "a kind that is neither change nor question": (
        proposals_file(proposal().replace("· change ·", "· merge ·")),
        f"{PROPOSALS}:4: malformed proposal: expected `- [ ] P<number> · <change|question> · ",
    ),
    "a line that isn't a proposal": (
        proposals_file(proposal(), "Please answer by Friday."),
        f"{PROPOSALS}:5: malformed proposal",
    ),
    "a proposal that cites nothing": (
        proposals_file(proposal(cites="").rstrip()),
        f"{PROPOSALS}:4: malformed proposal",
    ),
    "an id that isn't P and a number": (
        proposals_file(proposal().replace("P1 ·", "Q1 ·")),
        f"{PROPOSALS}:4: malformed proposal",
    ),
    "a date that is no day": (
        proposals_file(proposal().replace(f"proposed {TODAY}", "proposed 2026-02-30")),
        f"{PROPOSALS}:4: malformed proposal",
    ),
    "text holding the separator": (
        proposals_file(proposal(text="Are they one · or two?")),
        f"{PROPOSALS}:4: malformed proposal",
    ),
    "a duplicate id": (
        proposals_file(proposal(), proposal(text="Is [[dave]] David Kim?")),
        f"{PROPOSALS}:5: P1 is already the id of the proposal on line 4",
    ),
    "text over 200 characters": (
        proposals_file(proposal(text=padded(201))),
        f"{PROPOSALS}:4: P1's text is 201 characters, more than the 200 a proposal may have",
    ),
    "a URL": (
        proposals_file(proposal(text=QUESTION + " See https://dave.example/rsvp")),
        f"{PROPOSALS}:4: P1's text holds a URL (https://dave.example/rsvp)",
    ),
    "a URL without a scheme": (
        proposals_file(proposal(text=QUESTION + " See www.dave.example")),
        f"{PROPOSALS}:4: P1's text holds a URL (www.dave.example)",
    ),
    "a host and a path": (
        proposals_file(proposal(text=QUESTION + " See dave.example/rsvp")),
        f"{PROPOSALS}:4: P1's text holds a URL (dave.example/rsvp)",
    ),
    "a Markdown link": (
        proposals_file(proposal(text="Are [[dave]] and [David](dave) the same person?")),
        f"{PROPOSALS}:4: P1's text holds a URL (](dave))",
    ),
    "a citation to an episode that isn't in L0": (
        proposals_file(proposal(cites="[ep:gmail_0000000000000000]")),
        f"{PROPOSALS}:4: cites gmail_0000000000000000, which isn't in L0",
    ),
    "a link that doesn't resolve": (
        proposals_file(proposal(text="Are [[jordan-reyes]] and [[david-kim]] the same person?")),
        f"{PROPOSALS}:4: P1 links [[jordan-reyes]], which has no page",
    ),
    "a missing section": (
        proposals_file(proposal()).replace("## Answered\n", ""),
        f"{PROPOSALS}:3: wrong sections: expected `## Open`, `## Answered`, in that order; got "
        "`## Open`",
    ),
    "another section": (
        proposals_file(proposal()) + "\n## Declined\n",
        f"{PROPOSALS}:3: wrong sections",
    ),
    "text before the sections": (
        proposals_file(proposal()).replace("# Proposals\n", "# Proposals\nAnswer in chat.\n"),
        f"{PROPOSALS}:2: text before its first section",
    ),
    "an open proposal under Answered": (
        proposals_file(proposal(), answered=(proposal(2),)),
        f"{PROPOSALS}:7: P2 is open, under `## Answered`",
    ),
    "an answered proposal under Open": (
        proposals_file(proposal(), ANSWERED_LINE),
        f"{PROPOSALS}:5: P2 is answered, under `## Open`",
    ),
    "a checked proposal that says no answer": (
        proposals_file(proposal(), answered=(proposal(2).replace("- [ ]", "- [x]"),)),
        f"{PROPOSALS}:7: malformed proposal",
    ),
    "an unchecked proposal that says its answer": (
        proposals_file(proposal(), ANSWERED_LINE.replace("- [x]", "- [ ]")),
        f"{PROPOSALS}:5: malformed proposal",
    ),
    "a question answered as a change is": (
        proposals_file(proposal(), answered=(ANSWERED_LINE.replace("· answered", "· accepted"),)),
        f"{PROPOSALS}:7: malformed proposal",
    ),
    "a change answered as a question is": (
        proposals_file(proposal(), answered=(ANSWERED_LINE.replace("question", "change"),)),
        f"{PROPOSALS}:7: malformed proposal",
    ),
    "an answered proposal that cites no answer": (
        proposals_file(proposal(), answered=(ANSWERED_LINE.removesuffix(f" [ep:{HOTEL}]"),)),
        f"{PROPOSALS}:7: malformed proposal",
    ),
}


@pytest.mark.parametrize("error", LINT_ERRORS, ids=list(LINT_ERRORS))
def test_a_lint_error_in_the_proposals_resumes_the_session_with_the_finding(
    data_root: Path, error: str
):
    run_with_pages(data_root)
    written, told = LINT_ERRORS[error]
    script(
        data_root,
        dreaming=[{"write": PROPOSALS, "text": written}, reporting(proposed())],
        dreaming_resume=[proposing(proposal()), reporting(proposed())],
    )

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert "--resume" not in started["argv"]
    assert flag(resumed["argv"], "--resume") == "session-1"
    assert told in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())
    assert fields(commit_messages(data_root)[0])["Dreaming-Resumes"] == "1"


def test_a_lint_error_left_in_the_proposals_after_two_resumes_discards_the_run(
    data_root: Path, capsys
):
    run_with_pages(data_root)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    written, told = LINT_ERRORS["a URL"]
    attempt = [{"write": PROPOSALS, "text": written}, reporting(proposed())]
    script(data_root, dreaming=attempt, dreaming_resume=attempt)
    capsys.readouterr()

    assert dream() != 0

    error = capsys.readouterr().err
    assert "the dreaming session left 1 lint errors after 2 resumes" in error
    assert told in error
    assert len(sessions(data_root, "dreaming")) == 3
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def commit_by_hand(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, so the cursor stays: the
    wiki as earlier dreaming runs and the owner's answers would have left it."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).parent.mkdir(parents=True, exist_ok=True)
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


def test_every_proposal_a_run_adds_is_named_by_an_outcome(data_root: Path):
    run_with_pages(data_root)
    stray = proposal(2, "Is [[dave]] invited to the wedding?", kind="question")
    script(
        data_root,
        dreaming=[proposing(proposal(), stray), reporting(proposed("P1"))],
        dreaming_resume=[proposing(proposal()), reporting(proposed("P1"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert "P2 is new in proposals.md, but no outcome names it" in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())


LAPSED = "Close these 2 loops on [[wedding]] as lapsed? Nothing has come since."


def test_several_candidates_can_be_gathered_into_one_proposal(data_root: Path):
    run_with_pages(data_root, pages(loop("the room list", 40), loop("the menu", 40)))
    gathered = proposing(proposal(text=LAPSED))
    script(
        data_root,
        dreaming=[
            gathered,
            reporting(
                proposed("P1", "c01"),
                proposed("P1", "c02"),
                {"candidate": "c03", "outcome": "dismissed"},
            ),
        ],
    )

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    summary, listed, _ = commit_messages(data_root)[0].split("\n\n")
    assert summary == "dreaming run: 3 candidates, 0 applied, 2 proposed, 1 dismissed"
    assert listed.splitlines()[1:] == [
        f"- c01 · loop-aging · {TOPIC} [ep:{HOTEL}] · proposed: P1",
        f"- c02 · loop-aging · {TOPIC} [ep:{HOTEL}] · proposed: P1",
        f"- c03 · duplicate · {DAVID}, {DAVE} · dismissed",
    ]
    assert wiki_files(data_root)[PROPOSALS] == gathered["text"]


def test_an_outcome_can_name_a_proposal_an_earlier_run_left_open(data_root: Path):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    history = commit_messages(data_root)
    # The owner hasn't answered yet, so the same duplicate is asked nothing new.
    script(data_root, dreaming=[reporting(proposed("P1"))])

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    assert commit_messages(data_root)[1:] == history
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())


EARLIER = proposal(2, "Is [[dave]] invited to the wedding?", kind="question")
"""A proposal an earlier run left open, numbered above a gap."""
ANSWERED_EARLIER = ANSWERED_LINE.replace("P2 ·", "P5 ·")
"""A proposal the owner answered, with the highest id so far."""


def earlier_proposals(data_root: Path) -> str:
    """Commits a `proposals.md` holding P2, open, and P5, answered, and returns it."""
    text = proposals_file(EARLIER, answered=(ANSWERED_EARLIER,))
    commit_by_hand(data_root, {PROPOSALS: text})
    return text


def test_the_session_gets_the_open_proposals_and_the_next_id(data_root: Path):
    run_with_pages(data_root)
    earlier_proposals(data_root)
    script(data_root, dreaming=[reporting(DISMISSED)])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert session["proposals"] == [
        {
            "id": "P2",
            "kind": "question",
            "text": "Is [[dave]] invited to the wedding?",
            "proposed": TODAY,
            "episodes": [HOTEL],
        }
    ]
    assert f"`{data_root / 'wiki' / PROPOSALS}`" in session["prompt"]
    # One past the highest id the file holds, answered proposals included.
    assert "number them from P6" in session["prompt"]


def test_a_wiki_with_no_proposals_starts_numbering_at_one(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=[reporting(DISMISSED)])

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert session["proposals"] == []
    assert "number them from P1" in session["prompt"]
    assert PROPOSALS not in wiki_files(data_root)


def test_a_proposals_id_is_never_reused_even_one_below_an_answered_proposals(data_root: Path):
    run_with_pages(data_root)
    earlier = earlier_proposals(data_root)

    def adding(number: int) -> dict:
        added = earlier.replace(EARLIER, f"{EARLIER}\n{proposal(number)}")
        return {"write": PROPOSALS, "text": added}

    script(
        data_root,
        dreaming=[adding(3), reporting(proposed("P3"))],
        dreaming_resume=[adding(6), reporting(proposed("P6"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert (
        "P3 is new, but ids up to P5 have been given out: an id is never reused, so number new "
        "proposals from P6" in resumed["prompt"]
    )
    assert wiki_files(data_root)[PROPOSALS] == adding(6)["text"]
    assert commit_messages(data_root)[0].split("\n\n")[1].splitlines()[1] == (
        f"- c01 · duplicate · {DAVID}, {DAVE} · proposed: P6"
    )


# What a dreaming session does to the proposals an earlier run left (P2, open, and P5,
# answered) as it adds its own, and what it's told when it's resumed.
TAMPERED = {
    "an open proposal reworded": (
        lambda text: text.replace("invited to the wedding", "coming to the wedding"),
        f"P2 was changed: a dreaming run only adds proposals. Put its line back as it was: "
        f"`{EARLIER}`",
    ),
    "an open proposal removed": (
        lambda text: text.replace(EARLIER + "\n", ""),
        f"P2 was removed: a dreaming run only adds proposals. Put its line back as it was: "
        f"`{EARLIER}`",
    ),
    "an open proposal marked answered": (
        lambda text: text.replace(EARLIER + "\n", "").replace(
            ANSWERED_EARLIER,
            ANSWERED_EARLIER + "\n" + ANSWERED_LINE.replace("Did [[david-kim]] send", "Is"),
        ),
        "P2 was changed: a dreaming run only adds proposals.",
    ),
    "an answered proposal removed": (
        lambda text: text.replace(ANSWERED_EARLIER + "\n", ""),
        f"P5 was removed: a dreaming run only adds proposals. Put its line back as it was: "
        f"`{ANSWERED_EARLIER}`",
    ),
}


@pytest.mark.parametrize("tampering", TAMPERED, ids=list(TAMPERED))
def test_a_dreaming_run_only_adds_proposals(data_root: Path, tampering: str):
    run_with_pages(data_root)
    earlier = earlier_proposals(data_root)
    added = earlier.replace(EARLIER, f"{EARLIER}\n{proposal(6)}")
    tamper, told = TAMPERED[tampering]
    script(
        data_root,
        dreaming=[{"write": PROPOSALS, "text": tamper(added)}, reporting(proposed("P6"))],
        dreaming_resume=[{"write": PROPOSALS, "text": added}, reporting(proposed("P6"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert told in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == added


SUGGESTION = proposal(
    2,
    "Organization pages keep a phone number in Notes. Add a Contact section to their template?",
)
SUGGESTED = {"outcome": "proposed", "proposal": "P2"}
"""A template suggestion: an outcome for no candidate."""


def test_a_template_suggestion_is_an_extra_outcome_with_no_candidate(data_root: Path):
    run_with_pages(data_root)
    script(
        data_root,
        dreaming=[proposing(proposal(), SUGGESTION), reporting(proposed("P1"), SUGGESTED)],
    )

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal(), SUGGESTION)
    summary, listed, last = commit_messages(data_root)[0].split("\n\n")
    # The counts are of candidates; the suggestion is recorded beside them.
    assert summary == "dreaming run: 1 candidates, 0 applied, 1 proposed, 0 dismissed"
    assert listed.splitlines() == [
        "Candidates:",
        f"- c01 · duplicate · {DAVID}, {DAVE} · proposed: P1",
    ]
    recorded = fields(last)
    assert recorded["Dreaming-Template-Suggestion"] == "P2"
    assert (recorded["Dreaming-Candidates"], recorded["Dreaming-Proposed"]) == ("1", "1")


def test_a_run_without_a_template_suggestion_records_none(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=[proposing(proposal()), reporting(proposed("P1"))])

    assert dream() == 0

    assert "Dreaming-Template-Suggestion" not in fields(commit_messages(data_root)[0])


THIRD = proposal(3, "Topic pages have no place for a venue. Add a Where section?")

# Outcomes for no candidate that the host can't accept, beside the duplicate's, proposed P1:
# what the session wrote and reported, and what it's told when it's resumed.
INVALID_SUGGESTIONS = {
    "two template suggestions": (
        [
            proposing(proposal(), SUGGESTION, THIRD),
            reporting(proposed("P1"), SUGGESTED, {**SUGGESTED, "proposal": "P3"}),
        ],
        "2 outcomes have no candidate: a run makes at most one template suggestion",
    ),
    "a suggestion naming a proposal that isn't in the file": (
        [proposing(proposal()), reporting(proposed("P1"), SUGGESTED)],
        "the template suggestion names P2, which isn't a proposal this run added",
    ),
    "a suggestion naming a candidate's proposal": (
        [proposing(proposal()), reporting(proposed("P1"), {**SUGGESTED, "proposal": "P1"})],
        "the template suggestion names P1, which a candidate's outcome names too",
    ),
    "an applied outcome with no candidate": (
        [
            proposing(proposal()),
            reporting(proposed("P1"), {"outcome": "applied", "pages": [TOPIC]}),
        ],
        "outcomes.1.applied.candidate: Field required",
    ),
    "a dismissed outcome with no candidate": (
        [proposing(proposal()), reporting(proposed("P1"), {"outcome": "dismissed"})],
        "outcomes.1.dismissed.candidate: Field required",
    ),
}


@pytest.mark.parametrize("invalid", INVALID_SUGGESTIONS, ids=list(INVALID_SUGGESTIONS))
def test_an_outcome_for_no_candidate_is_one_template_suggestion_or_resumes_the_session(
    data_root: Path, invalid: str
):
    run_with_pages(data_root)
    first, told = INVALID_SUGGESTIONS[invalid]
    script(
        data_root,
        dreaming=first,
        dreaming_resume=[proposing(proposal()), reporting(proposed("P1"))],
    )

    assert dream() == 0

    _, resumed = sessions(data_root, "dreaming")
    assert told in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal())


def questions(count: int, first: int = 1) -> list[str]:
    """`count` open proposals, numbered from `first`."""
    return [
        proposal(number, f"Is [[dave]] guest number {number}?", kind="question")
        for number in range(first, first + count)
    ]


def asking(count: int, candidates: int, *, first: int = 1, standing: tuple[str, ...] = ()) -> list:
    """What a session does to propose its first `count` candidates of `candidates`, each with a
    proposal of its own numbered from `first`, after the `standing` lines, and dismiss the
    rest."""
    outcomes = [
        proposed(f"P{first + n}", f"c{n + 1:02d}")
        if n < count
        else {"candidate": f"c{n + 1:02d}", "outcome": "dismissed"}
        for n in range(candidates)
    ]
    return [proposing(*standing, *questions(count, first)), reporting(*outcomes)]


def aging_loops(count: int) -> dict[str, str]:
    """The pages with `count` loops open 40 days: `count` candidates, then the duplicate."""
    return pages(*[loop(f"item {number}", 40) for number in range(1, count + 1)])


def test_a_run_adds_at_most_five_proposals(data_root: Path):
    run_with_pages(data_root, aging_loops(6))
    script(data_root, dreaming=asking(6, 7), dreaming_resume=asking(5, 7))

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert "You may add at most 5 in this run" in started["prompt"]
    assert "this run adds 6 proposals, more than the 5 a run may add" in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(*questions(5))
    recorded = fields(commit_messages(data_root)[0])
    assert (recorded["Dreaming-Proposed"], recorded["Dreaming-Dismissed"]) == ("5", "2")


def test_the_cap_on_new_proposals_follows_the_configuration(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_NEW_PROPOSALS", "1")
    run_with_pages(data_root, aging_loops(2))
    script(data_root, dreaming=asking(2, 3), dreaming_resume=asking(1, 3))

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert "You may add at most 1 in this run" in started["prompt"]
    assert "this run adds 2 proposals, more than the 1 a run may add" in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(*questions(1))


ANSWERED_FIFTEENTH = ANSWERED_LINE.replace("P2 ·", "P15 ·")


def test_no_more_than_fifteen_proposals_are_open_at_once(data_root: Path):
    run_with_pages(data_root, aging_loops(2))
    # Fourteen are open; the one the owner answered doesn't count.
    standing = tuple(questions(14))
    commit_by_hand(
        data_root, {PROPOSALS: proposals_file(*standing, answered=(ANSWERED_FIFTEENTH,))}
    )

    def adding(count: int) -> list:
        write, report = asking(count, 3, first=16, standing=standing)
        write["text"] = proposals_file(
            *standing, *questions(count, 16), answered=(ANSWERED_FIFTEENTH,)
        )
        return [write, report]

    script(data_root, dreaming=adding(2), dreaming_resume=adding(1))

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert (
        "You may add at most 1 in this run (14 are open, and at most 15 may be open at once)"
        in started["prompt"]
    )
    assert (
        "16 proposals would be open, more than the 15 that may be open at once: 14 were open "
        "already, so add at most 1" in resumed["prompt"]
    )
    assert wiki_files(data_root)[PROPOSALS] == adding(1)[0]["text"]


def test_the_cap_on_open_proposals_follows_the_configuration(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_OPEN_PROPOSALS", "2")
    run_with_pages(data_root, aging_loops(2))
    standing = tuple(questions(2))
    commit_by_hand(data_root, {PROPOSALS: proposals_file(*standing)})
    script(
        data_root,
        dreaming=asking(1, 3, first=3, standing=standing),
        dreaming_resume=asking(0, 3, first=3, standing=standing),
    )

    assert dream() == 0

    started, resumed = sessions(data_root, "dreaming")
    assert "You may add none in this run: 2 are open already" in started["prompt"]
    assert (
        "3 proposals would be open, more than the 2 that may be open at once: 2 were open "
        "already, so add at most 0" in resumed["prompt"]
    )
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(*standing)


def test_a_cap_lowered_below_what_is_open_fails_no_run_that_adds_nothing(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(*questions(3))})
    monkeypatch.setenv("PA_LIBRARIAN_OPEN_PROPOSALS", "2")
    script(data_root, dreaming=[reporting(DISMISSED)])

    # The owner's answers reach the wiki through the nightly run, so it must still commit.
    assert dream() == 0
    ingest(
        data_root,
        {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")},
    )
    script(data_root)
    assert run_librarian() == 0

    assert wiki_files(data_root)[PROPOSALS] == proposals_file(*questions(3))


# Proposals in every shape the schema allows: what the session writes, proposing P1.
WELL_FORMED = {
    "no title line": proposals_file(proposal()).removeprefix("# Proposals\n\n"),
    "a question": proposals_file(
        proposal(text="Did [[david-kim]] get the room list?", kind="question")
    ),
    "a link shown under another name": proposals_file(
        proposal(text="Are [[dave|Dave]] and [[david-kim|David]] the same person?")
    ),
    "text of exactly 200 characters": proposals_file(proposal(text=padded(200))),
    "several citations": proposals_file(proposal(cites=f"[ep:{HOTEL}] [ep:{HOTEL}]")),
    "no link at all": proposals_file(proposal(text="Who is the Dave that sent the room list?")),
    "answered proposals below it": proposals_file(
        proposal(3),
        answered=(
            ANSWERED_LINE,
            ANSWERED_LINE.replace("P2 · question", "P1 · change").replace(
                "· answered", "· declined"
            ),
        ),
    ),
}


@pytest.mark.parametrize("shape", WELL_FORMED, ids=list(WELL_FORMED))
def test_a_well_formed_proposals_file_passes_lint(data_root: Path, shape: str):
    run_with_pages(data_root)
    written = WELL_FORMED[shape]
    if "## Answered\n-" in written:
        # The owner's answers are there already; the session adds P3 above them.
        commit_by_hand(data_root, {PROPOSALS: written.replace(proposal(3) + "\n", "")})
    new = "P3" if "## Answered\n-" in written else "P1"
    script(data_root, dreaming=[{"write": PROPOSALS, "text": written}, reporting(proposed(new))])

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    assert wiki_files(data_root)[PROPOSALS] == written


def test_a_wiki_with_no_proposals_file_still_lints_clean(data_root: Path):
    run_with_pages(data_root)
    script(data_root, dreaming=[reporting(DISMISSED)])

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    assert PROPOSALS not in wiki_files(data_root)
    assert fields(commit_messages(data_root)[0])["Dreaming-Dismissed"] == "1"


KITCHEN = "topics/kitchen.md"
KITCHEN_PAGE = textwrap.dedent(
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


def test_a_page_linked_only_from_a_proposal_is_still_an_orphan(data_root: Path):
    run_with_pages(data_root, {**pages(), KITCHEN: KITCHEN_PAGE})
    asked = proposal(text="What is [[kitchen]] part of?", kind="question")
    script(
        data_root,
        dreaming=[
            proposing(asked),
            reporting(proposed("P1", "c01"), {"candidate": "c02", "outcome": "dismissed"}),
        ],
    )

    assert dream() == 0

    [session] = sessions(data_root, "dreaming")
    assert [c["kind"] for c in session["candidates"]] == ["orphan", "duplicate"]
    # Asking about the page doesn't link it into the wiki: lint still warns about both.
    assert fields(commit_messages(data_root)[0])["Lint-Warnings"] == "2"


def test_after_a_merge_an_open_proposals_link_to_the_alias_goes_to_the_page(data_root: Path):
    written = pages()
    run_with_pages(data_root, written)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    # L0 settles it after all: David Kim's page takes Dave's, which is emptied.
    merged = written[DAVID].replace(
        "## Notes\n", f"## Notes\nGoes by Dave, who sent the room list [ep:{HOTEL}].\n"
    )
    script(
        data_root,
        dreaming=[
            {"write": DAVE, "text": ""},
            {"write": DAVID, "text": merged},
            reporting({"candidate": "c01", "outcome": "applied", "pages": [DAVE, DAVID]}),
        ],
    )

    assert dream() == 0

    assert len(sessions(data_root, "dreaming")) == 1
    files = wiki_files(data_root)
    assert DAVE not in files
    assert files[PROPOSALS] == proposals_file(
        proposal(text="Are [[david-kim|dave]] and [[david-kim]] the same person?")
    )


def index_opening(data_root: Path) -> list[str]:
    """The index's lines down to its first section."""
    lines = wiki_files(data_root)["index.md"].splitlines()
    return lines[: lines.index("## People")]


def test_the_index_opens_with_the_number_of_open_proposals_and_a_link_to_them(data_root: Path):
    run_with_pages(data_root)
    # Before the librarian has asked anything, there is nothing to link.
    assert index_opening(data_root) == ["# Index", "", "Open proposals: 0", ""]
    second = proposal(2, "Is [[dave]] invited to the wedding?", kind="question")
    script(
        data_root,
        dreaming=[proposing(proposal(), second), reporting(proposed("P1"), SUGGESTED)],
    )

    assert dream() == 0

    assert index_opening(data_root) == ["# Index", "", "Open proposals: 2, in [[proposals]]", ""]
    changed = git(data_root, "show", "--format=", "--name-only", "HEAD").split()
    assert changed == ["index.md", PROPOSALS]


def test_the_index_counts_only_the_proposals_the_owner_hasnt_answered(data_root: Path):
    run_with_pages(data_root)
    answered = ANSWERED_LINE.replace("P2 · question", "P1 · change").replace(
        "· answered", "· accepted"
    )
    commit_by_hand(data_root, {PROPOSALS: proposals_file(answered=(answered, ANSWERED_LINE))})
    ingest(
        data_root,
        {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")},
    )
    script(data_root)

    # The host regenerates the index on a nightly run too.
    assert run_librarian() == 0

    assert index_opening(data_root) == ["# Index", "", "Open proposals: 0", ""]


def dreaming_instructions(subsection: str | None = None) -> str:
    """The librarian project's instructions for the dreaming phase, as the checkout has them,
    with their whitespace collapsed: CLAUDE.md's Dreaming section, or one `### ` subsection of
    it."""
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    section = re.search(r"^## Dreaming\n(.*?)(?=^## )", claude_md, re.M | re.S)
    assert section is not None, "CLAUDE.md has no Dreaming section"
    text = section[1]
    if subsection is not None:
        found = re.search(rf"^### {subsection}\n(.*?)(?=^###? |\Z)", text, re.M | re.S)
        assert found is not None, subsection
        text = found[1]
    return " ".join(text.split())


def test_the_dreaming_instructions_say_when_to_propose():
    proposing_rules = dreaming_instructions("When to propose")

    for rule in (
        # The four cases the owner is asked about.
        "**A duplicate L0 can't settle.**",
        "**A current state that contradicts its decisions**, where L0 doesn't settle which is "
        "right.",
        "**Routine loops long past due with nothing since.**",
        "Gather them into one proposal",
        "**A template suggestion.**",
        "at most one a run",
        "with no `candidate`",
        # A proposal is in the librarian's voice, so mail can't put one to the owner.
        "Never cite suspicious mail",
        # And it stays short and plain.
        "at most 200 characters",
        "no URL",
    ):
        assert rule in proposing_rules, rule


def test_the_dreaming_instructions_widen_its_bounds_to_the_proposals():
    instructions = dreaming_instructions()

    assert "**proposed**" in instructions
    assert "and add proposals to `proposals.md`" in instructions
    assert "every proposal you added is named by a proposed outcome" in instructions
    # The kinds whose judgment used to end in a dismissal now point at the proposals.
    judging = dreaming_instructions("How to judge each kind")
    assert "When L0 shows neither, propose it" in judging
    assert "`proposed`" in dreaming_instructions("Outcomes")


def test_the_glossary_defines_a_proposal():
    glossary = (LIBRARIAN_PROJECT.parent / "GLOSSARY.md").read_text()
    entry = re.search(r"^\*\*Proposal\*\*:\n(.*?)\n_Avoid_: ", glossary, re.M | re.S)

    assert entry is not None
    for said in ("`proposals.md`", "proposed change", "question", "answers in chat"):
        assert said in entry[1], said
