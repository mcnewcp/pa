"""The owner answers a proposal, and the next nightly run applies the answer (#144).

Whole runs of `pa librarian run` over a temporary data root, through the stand-in `claude` (the
conftest at the repository root says what it does and how it's scripted). Each test builds a
wiki with a nightly run, plants the proposals an earlier dreaming run would have left, delivers
the owner's answer as a chat exchange, scripts what consolidation writes, and asserts on the
wiki, its git history, and what the session was given: the stand-in records the open proposals
listed under the prompt's `## Open proposals` heading as `proposals`.
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

from pa_core.normalizers.assistant_chat import encode_exchange, exchange_episode_id
from pa_home.cli import main
from pa_home.git_wiki import GitWikiRepository
from pa_home.librarian_project import LIBRARIAN_PROJECT

CHICAGO = ZoneInfo("America/Chicago")
"""The owner's time zone."""


def days_ago(days: int) -> str:
    """The date `days` days before today in the owner's time zone; runs are told the clock."""
    return (datetime.now(CHICAGO).date() - timedelta(days=days)).isoformat()


def at(days: int, clock: str) -> datetime:
    """`clock` in the owner's time zone, `days` days before today."""
    return datetime.combine(
        datetime.now(CHICAGO).date() - timedelta(days=days), time.fromisoformat(clock), CHICAGO
    )


# The wiki's mail and chats are dated by the clock, as the runs are: a page's age is measured
# from today, and one nothing has linked for 90 days is a candidate for dreaming.
ROOM_LIST_DAY = days_ago(10)
"""The day the later email of `run_with_pages` is logged on, and its pages were updated."""
HOTEL = "gmail_9ff7394d0df8584d"
"""The id of the episode `email()` makes by default."""


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    # Sent late in Chicago, which is already the next day in UTC.
    date: str = format_datetime(at(12, "21:30")),
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


CHAT_SESSION = "5b0f3c2e-8d41-4a7e-9c16-2f7d0a9e4b13"


def chat(message: str, owner: str, assistant: str, *, days: int = 0) -> bytes:
    """One exchange with the assistant, as the capture hook drops it in the inbox: the owner's
    turn and the assistant's reply, `days` days ago."""
    started = at(days, "07:15")
    return encode_exchange(
        session_id=CHAT_SESSION,
        message_id=message,
        started_at=started.isoformat(),
        ended_at=(started + timedelta(seconds=20)).isoformat(),
        turns=[("owner", owner), ("assistant", assistant)],
    )


def chat_id(message: str) -> str:
    """The id of the episode `chat(message, ...)` makes."""
    return exchange_episode_id(CHAT_SESSION, message)


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
    """David Kim's page, a second page for him under the name his own has as an alias, and his
    wedding's page with `loops` (one opened yesterday unless given)."""
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
EARLIER_CHAT = chat_id("earlier-answer")
"""The owner's answer to a proposal of an earlier week, in L0 from the first run on."""


def run_with_pages(data_root: Path, written: dict[str, str] | None = None) -> None:
    """One nightly run over the hotel email, a later one sending the room list, and a chat in
    which the owner answered an earlier proposal; its consolidation writes `written` (`pages()`
    unless given)."""
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/room-list.eml": email(
                message_id="wedding-0002@example.net",
                labels="Sent",
                date=format_datetime(at(10, "09:00")),
                sender="Argus McNevans <argus@example.com>",
                to="David Kim <dkim@example.net>",
                subject="Room list",
                body="Here is the room list.",
            ),
            "assistant_chat/earlier.json": chat(
                "earlier-answer",
                "About P2: yes, David sent me the room list last week.",
                "Noted on P2: David Kim sent the room list. The wiki will show it after the "
                "next librarian run.",
                days=9,
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


QUESTION = "Are [[dave]] and [[david-kim]] the same person?"


def proposal(
    number: int = 1, text: str = QUESTION, *, kind: str = "change", cites: str = f"[ep:{HOTEL}]"
) -> str:
    """An open proposal's line, proposed three days ago."""
    return f"- [ ] P{number} · {kind} · {text} · proposed {days_ago(3)} {cites}"


def answered(line: str, how: str, answer: str, *, day: str = TODAY) -> str:
    """The open proposal `line` as the owner's `answer` (a chat's episode id) resolves it."""
    proposed = f" · proposed {days_ago(3)} "
    checked = line.replace("- [ ]", "- [x]").replace(proposed, f"{proposed}· {how} {day} ")
    return f"{checked} [ep:{answer}]"


EARLIER = answered(
    proposal(2, "Did [[david-kim]] send the room list?", kind="question"),
    "answered",
    EARLIER_CHAT,
    day=days_ago(9),
)
"""A proposal the owner answered before, as that week's run left it."""


def proposals_file(*opened: str, answered: tuple[str, ...] = (EARLIER,)) -> str:
    """`proposals.md` holding the `opened` lines under Open and the `answered` ones under
    Answered: the earlier answer alone, unless given."""
    return "\n".join(["# Proposals", "", "## Open", *opened, "", "## Answered", *answered, ""])


def commit_by_hand(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, so the cursor stays: the
    wiki as earlier dreaming runs and the owner's answers would have left it."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).parent.mkdir(parents=True, exist_ok=True)
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


def gift() -> dict[str, bytes]:
    """New mail for a night on which the owner answered nothing."""
    return {"gmail/gift.eml": email(message_id="wedding-0003@example.net", subject="Gift")}


def test_consolidation_is_given_the_open_proposals(data_root: Path):
    run_with_pages(data_root)
    second = proposal(3, "Is [[dave]] invited to the wedding?", kind="question")
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal(), second)})
    ingest(data_root, gift())
    script(data_root)

    assert run_librarian() == 0

    session = sessions(data_root, "consolidation")[-1]
    # The open ones only, as dreaming is given them; the answered one is history.
    assert session["proposals"] == [
        {
            "id": "P1",
            "kind": "change",
            "text": QUESTION,
            "proposed": days_ago(3),
            "episodes": [HOTEL],
        },
        {
            "id": "P3",
            "kind": "question",
            "text": "Is [[dave]] invited to the wedding?",
            "proposed": days_ago(3),
            "episodes": [HOTEL],
        },
    ]
    assert f"`{data_root / 'wiki' / PROPOSALS}`" in session["prompt"]
    # The observations and the reference counts are still the phase's own inputs.
    assert len(session["inputs"]) == 2
    # Nothing was answered, so the file is as it was.
    assert wiki_files(data_root)[PROPOSALS] == proposals_file(proposal(), second)


def test_a_wiki_with_nothing_open_gives_consolidation_no_proposals(data_root: Path):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file()})
    ingest(data_root, gift())
    script(data_root)

    assert run_librarian() == 0

    first, last = sessions(data_root, "consolidation")
    # Neither a wiki that has asked nothing nor one whose every proposal is answered.
    assert first["proposals"] is None
    assert last["proposals"] is None
    assert "## Open proposals" not in last["prompt"]


ANSWER = chat_id("answer")
"""The exchange in which the owner answers P1."""


def answer(owner: str = "On P1: yes, Dave is David Kim. Same person.") -> dict[str, bytes]:
    """The owner's answer to P1, captured as a chat exchange."""
    return {
        "assistant_chat/answer.json": chat(
            "answer",
            owner,
            "Noted on P1: Dave and David Kim are the same person. The wiki will show it after "
            "the next librarian run.",
        )
    }


def merged() -> list[dict]:
    """What consolidation does to the pages to apply a yes to P1: David Kim's page takes
    Dave's, which is emptied for the host to remove."""
    david = pages()[DAVID].replace(
        "## Notes\n", f"## Notes\nGoes by Dave, by the owner's word [ep:{ANSWER}].\n"
    )
    return [{"write": DAVE, "text": ""}, {"write": DAVID, "text": david}]


def resolving(*lines: str, opened: tuple[str, ...] = ()) -> dict:
    """The stand-in's action leaving `proposals.md` with `lines` answered, after the earlier
    answer, and `opened` still open."""
    return {"write": PROPOSALS, "text": proposals_file(*opened, answered=(EARLIER, *lines))}


ACCEPTED = answered(proposal(), "accepted", ANSWER)
"""P1 as the owner's yes resolves it."""


def index_opening(data_root: Path) -> list[str]:
    """The index's lines down to its first section."""
    lines = wiki_files(data_root)["index.md"].splitlines()
    return lines[: lines.index("## People")]


def test_a_run_whose_batch_holds_the_owners_answer_commits_the_proposal_as_answered(
    data_root: Path,
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    history = commit_messages(data_root)
    ingest(data_root, answer())
    script(data_root, consolidation=[*merged(), resolving(ACCEPTED)])

    assert run_librarian() == 0

    # The answer came in the batch, and consolidation had the proposal it answers.
    [exchange] = sessions(data_root, "observation")[-1]["episodes"]
    assert (exchange["id"], exchange["kind"]) == (ANSWER, "chat")
    assert exchange["owner_turns"] == ["On P1: yes, Dave is David Kim. Same person."]
    session = sessions(data_root, "consolidation")[-1]
    assert [asked["id"] for asked in session["proposals"]] == ["P1"]
    # One commit: the proposal under Answered, citing the exchange last, and the answer applied.
    assert commit_messages(data_root)[1:] == history
    files = wiki_files(data_root)
    assert DAVE not in files
    assert f"Goes by Dave, by the owner's word [ep:{ANSWER}]." in files[DAVID]
    [resolved] = [line for line in files[PROPOSALS].splitlines() if "P1 ·" in line]
    # The host rewrote the link to the page that was merged away, as it does everywhere.
    assert resolved == (
        "- [x] P1 · change · Are [[david-kim|dave]] and [[david-kim]] the same person? · "
        f"proposed {days_ago(3)} · accepted {TODAY} [ep:{HOTEL}] [ep:{ANSWER}]"
    )
    assert files[PROPOSALS].index("## Answered") < files[PROPOSALS].index(resolved)
    assert index_opening(data_root) == ["# Index", "", "Open proposals: 0", ""]
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""
    # The next run is given nothing to resolve.
    ingest(data_root, gift())
    script(data_root)
    assert run_librarian() == 0
    assert sessions(data_root, "consolidation")[-1]["proposals"] is None


INVITED = proposal(3, "Is [[dave]] invited to the wedding?", kind="question")
"""A second open proposal, which the owner's answer says nothing about."""
NEW = proposal(4, "Is [[wedding]] still on Oct 24?", kind="question")
"""A proposal the file doesn't hold."""
ONLY_BY_RESOLVING = (
    "consolidation may change proposals.md only by resolving an open proposal from the owner's "
    "answer"
)

# What a consolidation whose batch holds the owner's answer to P1 does to `proposals.md` (P1 and
# P3 open, P2 answered), and what the failed run says.
NOT_RESOLVING = {
    "a proposal added": (
        {"write": PROPOSALS, "text": proposals_file(proposal(), INVITED, NEW)},
        "P4 was added: proposals are added by dreaming alone",
    ),
    "a proposal added beside the resolution": (
        resolving(ACCEPTED, opened=(INVITED, NEW)),
        "P4 was added: proposals are added by dreaming alone",
    ),
    "a proposal added already answered": (
        resolving(ACCEPTED, answered(NEW, "answered", ANSWER), opened=(INVITED,)),
        "P4 was added: proposals are added by dreaming alone",
    ),
    "an open proposal reworded": (
        resolving(ACCEPTED, opened=(INVITED.replace("invited to", "coming to"),)),
        f"P3 was changed: put its line back as it was: `{INVITED}`",
    ),
    "an open proposal removed": (
        resolving(ACCEPTED),
        f"P3 was removed: put its line back as it was: `{INVITED}`",
    ),
    "an answered proposal changed": (
        {
            "write": PROPOSALS,
            "text": proposals_file(INVITED, answered=(EARLIER.replace(HOTEL, ANSWER), ACCEPTED)),
        },
        f"P2 was changed: put its line back as it was: `{EARLIER}`",
    ),
    "an answered proposal removed": (
        {"write": PROPOSALS, "text": proposals_file(INVITED, answered=(ACCEPTED,))},
        f"P2 was removed: put its line back as it was: `{EARLIER}`",
    ),
    "the proposal reworded as it is resolved": (
        resolving(ACCEPTED.replace("the same person", "one person"), opened=(INVITED,)),
        "P1 was changed as it was resolved: a resolved proposal keeps its id, kind, text, "
        "proposed date, and citations",
    ),
    "the proposal's kind changed as it is resolved": (
        resolving(
            ACCEPTED.replace("· change ·", "· question ·").replace("· accepted", "· answered"),
            opened=(INVITED,),
        ),
        "P1 was changed as it was resolved",
    ),
    "the proposal's own citation dropped as it is resolved": (
        resolving(ACCEPTED.replace(f"[ep:{HOTEL}]", f"[ep:{ANSWER}]"), opened=(INVITED,)),
        "P1 was changed as it was resolved",
    ),
    "the file rewritten with nothing resolved": (
        {
            "write": PROPOSALS,
            "text": proposals_file(proposal(), INVITED).replace("# Proposals", "# Questions"),
        },
        "no proposal was resolved",
    ),
    "the file removed": (
        {"delete": PROPOSALS},
        f"P1 was removed: put its line back as it was: `{proposal()}`",
    ),
}


@pytest.mark.parametrize("change", NOT_RESOLVING, ids=list(NOT_RESOLVING))
def test_a_consolidation_that_changes_the_proposals_other_than_by_resolving_one_fails_the_run(
    data_root: Path, capsys, change: str
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal(), INVITED)})
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    ingest(data_root, answer())
    action, reason = NOT_RESOLVING[change]
    script(data_root, consolidation=[*merged(), action])
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert "the consolidation session changed proposals.md" in error
    assert reason in error
    assert ONLY_BY_RESOLVING in error
    # It fails at once, as any change out of bounds does, and nothing of the run is kept.
    assert len(sessions(data_root, "consolidation")) == 2
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history
    # The owner's answer is still new for the next run.
    script(data_root)
    assert run_librarian() == 0
    assert [e["id"] for e in sessions(data_root, "observation")[-1]["episodes"]] == [ANSWER]


def test_the_control_for_those_failures_resolving_one_of_two_open_proposals_commits(
    data_root: Path,
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal(), INVITED)})
    ingest(data_root, answer())
    script(data_root, consolidation=[*merged(), resolving(ACCEPTED, opened=(INVITED,))])

    assert run_librarian() == 0

    lines = wiki_files(data_root)[PROPOSALS].splitlines()
    assert lines[lines.index("## Open") + 1] == INVITED.replace("[[dave]]", "[[david-kim|dave]]")
    assert index_opening(data_root)[2] == "Open proposals: 1, in [[proposals]]"


def episode(data_root: Path, subject: str) -> str:
    """The id of the first run's email with that subject."""
    [found] = [
        e["id"]
        for e in sessions(data_root, "observation")[0]["episodes"]
        if e["subject"] == subject
    ]
    return found


# What an answered proposal's last citation may not be: the owner's word in chat alone
# resolves one.
NOT_THE_OWNERS_CHAT = {
    "mail the owner received": "Hotel block",
    "mail the owner sent": "Room list",
}


@pytest.mark.parametrize("cited", NOT_THE_OWNERS_CHAT, ids=list(NOT_THE_OWNERS_CHAT))
def test_lint_fails_an_answered_proposal_whose_last_citation_isnt_the_owners_chat(
    data_root: Path, cited: str
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    ingest(data_root, answer())
    last = episode(data_root, NOT_THE_OWNERS_CHAT[cited])
    script(
        data_root,
        consolidation=[*merged(), resolving(answered(proposal(), "accepted", last))],
        consolidation_resume=[resolving(ACCEPTED)],
    )

    assert run_librarian() == 0

    started, resumed = sessions(data_root, "consolidation")[-2:]
    assert "--resume" not in started["argv"]
    assert flag(resumed["argv"], "--resume") == "session-1"
    assert (
        f"{PROPOSALS}:7: P1 is answered, but its last citation, {last}, isn't one of the owner's "
        "chat exchanges: only the owner's answer in chat resolves a proposal, cited last"
    ) in resumed["prompt"]
    assert f"[ep:{HOTEL}] [ep:{ANSWER}]" in wiki_files(data_root)[PROPOSALS]
    assert "Consolidation-Resumes: 1" in commit_messages(data_root)[0]


def test_a_proposal_resolved_without_the_owners_chat_after_two_resumes_discards_the_run(
    data_root: Path, capsys
):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    # Mail saying they are one person is evidence for a page, never an answer to the proposal.
    ingest(data_root, gift())
    attempt = [resolving(answered(proposal(), "accepted", HOTEL))]
    script(data_root, consolidation=attempt, consolidation_resume=attempt)
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert "the consolidation session left 1 lint errors after 2 resumes" in error
    assert "isn't one of the owner's chat exchanges" in error
    assert len(sessions(data_root, "consolidation")) == 4
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def test_a_proposal_copied_under_answered_and_left_open_resumes_the_session(data_root: Path):
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(proposal())})
    ingest(data_root, answer())
    script(
        data_root,
        consolidation=[*merged(), resolving(ACCEPTED, opened=(proposal(),))],
        consolidation_resume=[resolving(ACCEPTED)],
    )

    assert run_librarian() == 0

    resumed = sessions(data_root, "consolidation")[-1]
    assert f"{PROPOSALS}:8: P1 is already the id of the proposal on line 4" in resumed["prompt"]
    assert wiki_files(data_root)[PROPOSALS].count("P1 ·") == 1


STARTED = {
    "holding a proposal": (proposals_file(NEW, answered=()), "P4 was added"),
    "holding none": (proposals_file(answered=()), "no proposal was resolved"),
}


@pytest.mark.parametrize("started", STARTED, ids=list(STARTED))
def test_a_consolidation_cant_start_the_proposals_file(data_root: Path, capsys, started: str):
    run_with_pages(data_root)
    history = commit_messages(data_root)
    ingest(data_root, gift())
    written, reason = STARTED[started]
    script(data_root, consolidation=[{"write": PROPOSALS, "text": written}])
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert reason in error
    assert ONLY_BY_RESOLVING in error
    assert PROPOSALS not in wiki_files(data_root)
    assert commit_messages(data_root) == history


def split() -> list[dict]:
    """What consolidation does to the pages to apply a no to P1: both pages say they are two
    people, citing the owner."""
    note = f"## Notes\nNot the same person as the other Dave, by the owner's word [ep:{ANSWER}].\n"
    written = pages()
    return [
        {"write": path, "text": written[path].replace("## Notes\n", note)} for path in (DAVID, DAVE)
    ]


# How the owner's answer resolves an open proposal of each kind: the proposal, what the owner
# said, how the line says it was answered, and what consolidation does to the pages.
ANSWERS = {
    "a change accepted": (proposal(), "On P1: yes, same person.", "accepted", merged()),
    "a change declined": (proposal(), "On P1: no, they are two people.", "declined", split()),
    "a question answered": (
        proposal(text="Did [[david-kim]] get the room list?", kind="question"),
        "On P1: he did, he wrote back on Monday.",
        "answered",
        [],
    ),
}


@pytest.mark.parametrize("kind", ANSWERS, ids=list(ANSWERS))
def test_a_proposal_is_resolved_in_its_kinds_words_and_stays_in_the_file(
    data_root: Path, kind: str
):
    asked, said, how, applied = ANSWERS[kind]
    run_with_pages(data_root)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(asked)})
    ingest(data_root, answer(said))
    resolved = answered(asked, how, ANSWER)
    script(data_root, consolidation=[*applied, resolving(resolved)])

    assert run_librarian() == 0

    kept = wiki_files(data_root)[PROPOSALS].splitlines()
    assert f"· {how} {TODAY} " in kept[kept.index("## Answered") + 2]
    # A declined proposal is kept like any other, so dreaming can see it was asked, and the id
    # it took is never given out again.
    assert [line.split(" · ")[0] for line in kept if line.startswith("- ")] == [
        "- [x] P2",
        "- [x] P1",
    ]


LAPSED = "Close these 2 loops on [[wedding]] as lapsed? Nothing has come since."


def test_an_accepted_proposal_to_close_lapsed_loops_closes_them_citing_the_owners_answer(
    data_root: Path,
):
    stale = (loop("the room list", 40), loop("the menu", 40))
    run_with_pages(data_root, pages(*stale))
    asked = proposal(text=LAPSED)
    commit_by_hand(data_root, {PROPOSALS: proposals_file(asked)})
    ingest(data_root, answer("On P1: yes, close them both."))
    # A lapsed loop is a closed loop: checked, with the day and the owner's answer.
    closed = pages(
        *[line.replace("- [ ]", "- [x]") + f" · closed {TODAY} [ep:{ANSWER}]" for line in stale]
    )[TOPIC]
    script(
        data_root,
        consolidation=[
            {"write": TOPIC, "text": closed},
            resolving(answered(asked, "accepted", ANSWER)),
        ],
    )

    # Until the owner answers, lint warns about both loops.
    assert commit_messages(data_root)[0].count("a loop open 40 days") == 2

    assert run_librarian() == 0

    assert len(sessions(data_root, "consolidation")) == 2  # no resume: the lines pass lint
    files = wiki_files(data_root)
    loops = [line for line in files[TOPIC].splitlines() if "→ me" in line]
    assert len(loops) == 2
    assert all(
        line.startswith("- [x] ") and line.endswith(f"· closed {TODAY} [ep:{ANSWER}]")
        for line in loops
    )
    assert f"· accepted {TODAY} [ep:{HOTEL}] [ep:{ANSWER}]" in files[PROPOSALS]
    # Closed, they are no longer lint's to warn about.
    assert "a loop open" not in commit_messages(data_root)[0]


def instructions(section: str, subsection: str | None = None) -> str:
    """One `## ` section of the librarian's instructions, as the checkout has them, or one
    `### ` subsection of it, with its whitespace collapsed."""
    claude_md = (LIBRARIAN_PROJECT / "CLAUDE.md").read_text()
    found = re.search(rf"^## {section}\n(.*?)(?=^## |\Z)", claude_md, re.M | re.S)
    assert found is not None, section
    text = found[1]
    if subsection is not None:
        found = re.search(rf"^### {subsection}\n(.*?)(?=^###? |\Z)", text, re.M | re.S)
        assert found is not None, subsection
        text = found[1]
    return " ".join(text.split())


def test_the_corrections_instructions_say_how_to_resolve_a_proposal():
    resolving_rules = instructions("Corrections", "Resolving a proposal")

    for rule in (
        # The owner's answer is a correction, and only an owner turn in a chat is one.
        "The owner's answer to an open proposal is a correction",
        "Only an owner turn in a chat resolves a proposal",
        # It is applied to the pages, in each way a proposal can be answered.
        "**Accepted**",
        "**Declined**",
        "**Answered**",
        # A lapsed loop is a closed loop.
        "close each of them as any loop closes",
        "There is no lapsed state",
        # Then the proposal is resolved, and nothing else in the file changes.
        "move its line under `## Answered`",
        "citing the chat last",
        "stay exactly as they were",
        "the only change you make to `proposals.md`",
        "Never add a proposal",
    ):
        assert rule in resolving_rules, rule


def test_consolidations_instructions_bound_it_to_resolving_proposals():
    consolidation = instructions("Consolidation")

    assert "In `proposals.md` you may do one thing: resolve an open proposal" in consolidation
    assert "**Proposals.**" in consolidation
    assert "every open proposal the owner answered in this batch is resolved" in consolidation


def test_the_dreaming_instructions_never_ask_again_what_the_owner_answered():
    proposing_rules = instructions("Dreaming", "When to propose")

    assert "what `## Answered` already holds" in proposing_rules
    assert "a declined proposal stays there so that it isn't proposed again" in proposing_rules


def test_the_schema_says_only_the_owners_chat_answers_a_proposal():
    schema = " ".join((LIBRARIAN_PROJECT / "wiki-schema.md").read_text().split())

    assert "one of the owner's chat exchanges" in schema


def test_the_glossary_counts_an_answer_to_a_proposal_as_a_correction():
    glossary = (LIBRARIAN_PROJECT.parent / "GLOSSARY.md").read_text()
    entry = re.search(r"^\*\*Correction\*\*:\n(.*?)\n_Avoid_: ", glossary, re.M | re.S)

    assert entry is not None
    for said in ("answer to a proposal", "resolves the proposal"):
        assert said in entry[1], said
