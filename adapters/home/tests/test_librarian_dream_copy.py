"""`pa librarian dream TARGET`: dreaming a copy of a replay target, or of a cut's wiki, through
the stand-in `claude` (the conftest at the repository root says what it does and how it's
scripted).

A copy is dreamed at its as-of time, which its run log or its cut fixes, so the mail here is
dated by the calendar and not by the clock: nothing about a copy's dreaming run depends on the
day the test runs.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import ingest
from pa_home.cli import main
from pa_home.config import DataRoot, load_owner
from pa_home.cut import Cut, take_cut
from pa_home.filesystem_l0 import FilesystemL0

CHICAGO = ZoneInfo("America/Chicago")
"""The owner's time zone."""
AS_OF = "2026-10-14T20:00"
"""The as-of time the corpus is replayed up to, in the owner's time zone."""
AS_OF_UTC = "2026-10-15T01:00:00+00:00"
HOTEL = "gmail_9ff7394d0df8584d"
"""The id of the episode `email()` makes by default."""


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    date: str = "Mon, 12 Oct 2026 21:30:00 -0500",
    sender: str = "David Kim <dkim@example.net>",
    to: str = "Argus McNevans <argus@example.com>",
    subject: str = "Hotel block",
    body: str = "The hotel block closes on Oct 20.",
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


MAIL = {
    "gmail/hotel.eml": email(),
    "gmail/room-list.eml": email(
        message_id="wedding-0002@example.net",
        labels="Sent",
        date="Tue, 13 Oct 2026 09:00:00 -0500",
        sender="Argus McNevans <argus@example.com>",
        to="David Kim <dkim@example.net>",
        subject="Room list",
        body="Here is the room list.",
    ),
}
"""Two emails in the as-of time's week: the second, from the owner, sends the room list."""


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stand_in_claude: Path) -> Path:
    """The fictional owner, the stand-in `claude`, and a data root that doesn't exist until a
    test ingests into it: dreaming a copy over a corpus needs none."""
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
        "PA_LIBRARIAN_CANDIDATE_CAP",
        "PA_LIBRARIAN_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    return root


def script(data_root: Path, **phases: list[dict]) -> None:
    (data_root.parent / "script.json").write_text(json.dumps(phases))


def sessions(data_root: Path, phase: str | None = None) -> list[dict]:
    """What each session was given, in order; only `phase`'s sessions if given."""
    log = data_root.parent / "sessions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [r for r in records if phase is None or r["phase"] == phase]


def told_now(session: dict) -> str:
    """The time the session was told is now, as it was written."""
    argv = session["argv"]
    system_prompt = argv[argv.index("--append-system-prompt") + 1]
    stamp = re.search(r"; (\S+)\)\. Treat it as now\.", system_prompt)
    assert stamp is not None, system_prompt
    return stamp[1]


def git(target: Path, *args: str) -> str:
    return subprocess.run(
        ["git", f"--git-dir={target / 'wiki.git'}", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def commit_messages(target: Path) -> list[str]:
    """Every commit's message in the wiki in `target`, newest first."""
    return [m.strip() for m in git(target, "log", "--format=%B%x00").split("\0") if m.strip()]


def fields(message: str) -> dict[str, str]:
    """A commit message's `Field: value` lines: its last paragraph."""
    return dict(line.split(": ", 1) for line in message.strip().split("\n\n")[-1].splitlines())


def run_log(target: Path) -> list[dict]:
    return [json.loads(line) for line in (target / "run-log.jsonl").read_text().splitlines()]


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
ROOM_LIST = f"- [ ] [[david-kim]] → me: the room list · asked 2026-08-20 [ep:{HOTEL}]"
"""The wedding's loop, open 55 days at the as-of time: more than lint's 30."""
PAGES = {
    PERSON: textwrap.dedent(
        f"""\
        ---
        type: person
        aliases: [Dave]
        status: active
        summary: Old friend, getting married on Oct 24.
        updated: 2026-10-13
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
        updated: 2026-10-13
        ---
        # Wedding

        ## Current state
        The hotel block closes on Oct 20 [ep:{HOTEL}].

        ## Decisions

        ## Open loops
        {ROOM_LIST}

        ## Notes
        """
    ),
}
"""Dave's page and his wedding's, with nothing for a dreaming run to judge at the as-of time
but the wedding's aging loop."""
CLOSED = PAGES[TOPIC].replace(
    ROOM_LIST, ROOM_LIST.replace("- [ ]", "- [x]") + f" · closed 2026-10-13 [ep:{HOTEL}]"
)
"""The wedding's page with its loop closed."""
LINKS = {
    "Hotel block": ["david-kim", "wedding"],
    "Room list": ["david-kim", "wedding"],
    "Listing": ["nina-park"],
}
"""The slugs the stand-in's observations link, by the episode's subject. The owner's own mail,
the room list, links Dave, so his page is one the owner has engaged with."""
APPLIED = {"candidate": "c01", "outcome": "applied", "pages": [TOPIC]}
DISMISSED = {"candidate": "c01", "outcome": "dismissed"}


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A corpus of the wedding's mail, laid out as the inbox is."""
    directory = tmp_path / "corpus"
    for name, payload in MAIL.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(payload)
    return directory


def replayed(data_root: Path, corpus: Path, pages: dict[str, str] = PAGES) -> Path:
    """A replay target: the corpus replayed up to the as-of time, as one batch, whose
    consolidation writes `pages` (the wedding's unless given)."""
    target = data_root.parent / "replayed"
    script(
        data_root,
        observation=[{"observe": True, "links": LINKS}],
        consolidation=[{"write": path, "text": text} for path, text in pages.items()],
    )
    replay = ["librarian", "replay", str(target), "--corpus", str(corpus), "--as-of", AS_OF]
    assert main(replay) == 0
    return target


def copy_of(original: Path, name: str = "dreamed") -> Path:
    """A copy of a replay target as the evals tree keeps an eval wiki: its tree and its run
    log, without its git directory."""
    target = original.parent / name
    shutil.copytree(original / "wiki", target / "wiki")
    shutil.copyfile(original / "run-log.jsonl", target / "run-log.jsonl")
    return target


def dream(target: Path, *args: str | Path) -> int:
    return main(["librarian", "dream", str(target), *(str(arg) for arg in args)])


def test_dreaming_a_copy_of_a_replay_target_commits_in_the_copy_and_appends_to_its_run_log(
    data_root: Path, corpus: Path, capsys
):
    original = replayed(data_root, corpus)
    before = tree_state(original)
    [replay_run] = run_log(original)
    target = copy_of(original)
    script(data_root, dreaming=[{"write": TOPIC, "text": CLOSED}, {"outcomes": [APPLIED]}])
    capsys.readouterr()

    assert dream(target, "--corpus", corpus) == 0

    # One dreaming commit in the copy, which holds the copy's wiki as dreamed.
    head = git(target, "rev-parse", "HEAD").strip()
    assert f"committed {head[:8]}" in capsys.readouterr().out
    dreamed = fields(commit_messages(target)[0])
    assert (dreamed["Dreaming-Candidates"], dreamed["Dreaming-Applied"]) == ("1", "1")
    assert (target / "wiki" / TOPIC).read_text() == CLOSED
    assert git(target, f"--work-tree={target / 'wiki'}", "status", "--porcelain") == ""
    # The run is appended to the copy's run log, after the replay's.
    assert run_log(target) == [
        replay_run,
        {
            "run": "dreaming",
            "status": "committed",
            "commit": head,
            "as_of": AS_OF_UTC,
            "librarian_version": replay_run["librarian_version"],
            "dreaming_version": dreamed["Dreaming-Version"],
            "model": "claude-opus-5-5",
            "effort": "medium",
            "sessions": [
                {
                    "phase": "dreaming",
                    "session_id": "session-1",
                    "cost_usd": 0.0421,
                    "turns": 6,
                    "resumes": 0,
                }
            ],
            "cost_usd": 0.0421,
            "candidates": 1,
            "outcomes": {"applied": 1, "proposed": 0, "dismissed": 0},
            "lint_warnings": 0,
            "error": None,
        },
    ]
    # The original is untouched: its wiki, its history, and its run log.
    assert tree_state(original) == before
    # And so is the data root, which a corpus needs none of.
    assert not data_root.exists()


def test_the_dreaming_session_is_told_the_copys_as_of_time_not_the_real_date(
    data_root: Path, corpus: Path
):
    target = copy_of(replayed(data_root, corpus))
    script(data_root, dreaming=[{"outcomes": [DISMISSED]}])

    assert dream(target, "--corpus", corpus) == 0

    [session] = sessions(data_root, "dreaming")
    assert told_now(session) == "2026-10-14T20:00:00-05:00"
    # The candidates are measured from it too: the loop's age is its age then.
    assert [candidate["message"] for candidate in session["candidates"]] == [
        "a loop open 55 days, more than 30: is it settled, or still owed?"
    ]
    assert fields(commit_messages(target)[0])["Dreaming-At"] == AS_OF_UTC


BUILT_BY = "0123456789ab"
"""A librarian version that isn't this checkout's."""


def built_by_an_earlier_librarian(target: Path) -> None:
    """Rewrites the copy's run log as that of a replay by another librarian version."""
    entries = run_log(target)
    (target / "run-log.jsonl").write_text(
        "".join(json.dumps({**entry, "librarian_version": BUILT_BY}) + "\n" for entry in entries)
    )


def test_a_copys_history_names_the_version_that_built_it_and_the_one_that_dreamed_it(
    data_root: Path, corpus: Path
):
    target = copy_of(replayed(data_root, corpus))
    built_by_an_earlier_librarian(target)
    undreamed = tree_state(target / "wiki")
    script(data_root, dreaming=[{"write": TOPIC, "text": CLOSED}, {"outcomes": [APPLIED]}])

    assert dream(target, "--corpus", corpus) == 0

    # The copy had no history, so it starts with its tree as copied, and who built it.
    dreamed, start, _ = commit_messages(target)
    assert fields(start) == {"Librarian-Version": BUILT_BY, "Copy-As-Of": AS_OF_UTC}
    git(target, f"--work-tree={target / 'wiki'}", "checkout", "--quiet", "HEAD~1", "--", ".")
    assert tree_state(target / "wiki") == undreamed
    # The dreaming commit carries that version forward, beside its own.
    recorded = fields(dreamed)
    assert recorded["Librarian-Version"] == BUILT_BY
    assert len(recorded["Dreaming-Version"]) == 12 and recorded["Dreaming-Version"] != BUILT_BY
    entry = run_log(target)[-1]
    assert entry["librarian_version"] == BUILT_BY
    assert entry["dreaming_version"] == recorded["Dreaming-Version"]


def test_a_second_dreaming_run_of_a_copy_carries_on_from_the_first(data_root: Path, corpus: Path):
    target = copy_of(replayed(data_root, corpus))
    [observation] = sessions(data_root, "observation")
    [room_list] = [e["id"] for e in observation["episodes"] if e["subject"] == "Room list"]
    # The first run closes the loop, and leaves another one behind, as old.
    seating = f"- [ ] me → [[david-kim]]: the seating chart · promised 2026-08-25 [ep:{room_list}]"
    left = CLOSED.replace("\n## Notes", f"{seating}\n\n## Notes")
    script(data_root, dreaming=[{"write": TOPIC, "text": left}, {"outcomes": [APPLIED]}])
    assert dream(target, "--corpus", corpus) == 0
    first = git(target, "rev-parse", "HEAD").strip()
    script(data_root, dreaming=[{"outcomes": [DISMISSED]}])

    assert dream(target, "--corpus", corpus) == 0

    # The second run judges the wiki as the first left it, at the same as-of time.
    _, second = sessions(data_root, "dreaming")
    assert [(c["kind"], c["episodes"]) for c in second["candidates"]] == [
        ("loop-aging", [room_list])
    ]
    assert told_now(second) == "2026-10-14T20:00:00-05:00"
    assert (target / "wiki" / TOPIC).read_text() == left
    # Its commit follows the first's, in the copy's one history.
    head = git(target, "rev-parse", "HEAD").strip()
    assert git(target, "rev-parse", "HEAD~1").strip() == first
    assert [m.splitlines()[0] for m in commit_messages(target)] == [
        "dreaming run: 1 candidates, 0 applied, 0 proposed, 1 dismissed",
        "dreaming run: 1 candidates, 1 applied, 0 proposed, 0 dismissed",
        "Start the copy to dream",
        "Start the wiki",
    ]
    # And the run log has both, after the replay's run.
    assert [(e.get("run", "replay"), e["commit"]) for e in run_log(target)[1:]] == [
        ("dreaming", first),
        ("dreaming", head),
    ]


KITCHEN = textwrap.dedent(
    f"""\
    ---
    type: topic
    aliases: []
    status: active
    summary: The kitchen remodel.
    updated: 2026-10-13
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


def test_a_copy_with_more_candidates_than_the_cap_is_dreamed_run_by_run_until_none_is_left(
    data_root: Path, corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setenv("PA_LIBRARIAN_CANDIDATE_CAP", "1")
    target = copy_of(replayed(data_root, corpus, {**PAGES, "topics/kitchen.md": KITCHEN}))
    undreamed = len(sessions(data_root))
    # Each run is offered one candidate: it dismisses the orphan, then closes the loop.
    script(data_root, dreaming=[{"outcomes": [DISMISSED]}])
    assert dream(target, "--corpus", corpus) == 0
    script(data_root, dreaming=[{"write": TOPIC, "text": CLOSED}, {"outcomes": [APPLIED]}])
    assert dream(target, "--corpus", corpus) == 0
    capsys.readouterr()

    assert dream(target, "--corpus", corpus) == 0

    # The second run found the first's commit: the orphan it dismissed is held back, at the
    # copy's one as-of time, so the loop the cap left over is offered. The third has nothing
    # but the orphan, still held back.
    said = "librarian dream: nothing to judge; every candidate is held back (1)"
    assert said in capsys.readouterr().out
    offered = [
        [candidate["kind"] for candidate in session["candidates"]]
        for session in sessions(data_root)[undreamed:]
    ]
    assert offered == [["orphan"], ["loop-aging"]]
    assert [(e["candidates"], e["outcomes"]["applied"]) for e in run_log(target)[1:]] == [
        (1, 0),
        (1, 1),
    ]


NINA = "people/nina-park.md"
LISTING = email(
    message_id="listing-0001@lakeview.example",
    date="Mon, 12 Oct 2026 08:00:00 -0500",
    sender="Nina Park <nina@lakeview.example>",
    subject="Listing",
    body="A new listing near you.",
)
"""Mail from a realtor, which the owner never asked for and never answered."""
NINA_PARK = textwrap.dedent(
    f"""\
    ---
    type: person
    aliases: []
    status: active
    summary: A realtor who sent a listing.
    updated: 2026-10-13
    emails: []
    ---
    # Nina Park

    ## Current state
    Sent a listing [ep:{HOTEL}].

    ## Open loops

    ## Notes
    """
)
"""Her page, which only her own mail's observation links: one the owner never engaged with."""
RETIRING = {"candidate": "c02", "outcome": "applied", "pages": [NINA], "retired": ["nina-park"]}


def test_dreaming_a_copy_retires_a_page_as_dreaming_the_live_wiki_does(
    data_root: Path, corpus: Path, capsys
):
    (corpus / "gmail" / "listing.eml").write_bytes(LISTING)
    original = replayed(data_root, corpus, {**PAGES, NINA: NINA_PARK})
    before = tree_state(original)
    target = copy_of(original)
    # The session empties her page, which is all its file tools can do, and says it retired it.
    script(data_root, dreaming=[{"write": NINA, "text": ""}, {"outcomes": [DISMISSED, RETIRING]}])

    assert dream(target, "--corpus", corpus) == 0

    # The copy's session is offered the unengaged page, judged over the corpus the copy was
    # built over, and is told what is retired already.
    [session] = sessions(data_root, "dreaming")
    assert [(c["kind"], c["pages"]) for c in session["candidates"]] == [
        ("loop-aging", [TOPIC]),
        ("unengaged", [NINA]),
    ]
    assert "## Retired slugs" in session["prompt"]
    # The host removes the page and lists its slug, retired on the copy's as-of day in the
    # owner's time zone, in the copy's dreaming commit.
    assert not (target / "wiki" / NINA).exists()
    assert (target / "wiki" / "retired.md").read_text() == (
        "# Retired\n\n- nina-park · retired 2026-10-14\n"
    )
    assert "nina-park" not in (target / "wiki" / "index.md").read_text()
    head = git(target, "rev-parse", "HEAD").strip()
    changed = git(target, "show", "--format=", "--name-only", "HEAD").split()
    assert sorted(changed) == ["index.md", NINA, "retired.md"]
    assert git(target, f"--work-tree={target / 'wiki'}", "status", "--porcelain") == ""
    # The copy's run log records the run; lint's one warning is the loop left open. The day
    # log links her as it did.
    entry = run_log(target)[-1]
    assert (entry["run"], entry["status"], entry["commit"]) == ("dreaming", "committed", head)
    assert (entry["candidates"], entry["outcomes"], entry["lint_warnings"]) == (
        2,
        {"applied": 1, "proposed": 0, "dismissed": 1},
        1,
    )
    assert "[[nina-park]]" in (target / "wiki" / "days" / "2026-10-12.md").read_text()
    assert tree_state(original) == before
    # She stays retired: a second run of the copy has nothing left to judge, only the candidate
    # the first one dismissed.
    capsys.readouterr()
    assert dream(target, "--corpus", corpus) == 0
    said = "librarian dream: nothing to judge; every candidate is held back (1)"
    assert said in capsys.readouterr().out
    assert len(sessions(data_root, "dreaming")) == 1


def test_a_failed_dreaming_run_of_a_copy_is_logged_and_leaves_the_copy_as_it_was(
    data_root: Path, corpus: Path, capsys
):
    target = copy_of(replayed(data_root, corpus))
    undreamed = tree_state(target / "wiki")
    # It closes the loop, and never reports an outcome, however often it's asked.
    script(data_root, dreaming=[{"write": TOPIC, "text": CLOSED}])

    assert dream(target, "--corpus", corpus) == 1

    assert "The dreaming run was discarded" in capsys.readouterr().err
    assert tree_state(target / "wiki") == undreamed
    [_, failed] = run_log(target)
    assert failed["error"].endswith(
        "The dreaming run was discarded: the wiki is as last committed."
    )
    assert {**failed, "error": None} == {
        "run": "dreaming",
        "status": "failed",
        "commit": None,
        "as_of": AS_OF_UTC,
        "librarian_version": failed["dreaming_version"],
        "dreaming_version": failed["dreaming_version"],
        "model": "claude-opus-5-5",
        "effort": "medium",
        # Started, then resumed twice: three attempts' cost and turns.
        "sessions": [
            {
                "phase": "dreaming",
                "session_id": "session-1",
                "cost_usd": 0.1263,
                "turns": 18,
                "resumes": 2,
            }
        ],
        "cost_usd": 0.1263,
        "candidates": None,
        "outcomes": None,
        "lint_warnings": None,
        "error": None,
    }
    # The copy can be dreamed again.
    script(data_root, dreaming=[{"outcomes": [DISMISSED]}])
    assert dream(target, "--corpus", corpus) == 0
    assert [entry["status"] for entry in run_log(target)[1:]] == ["failed", "committed"]


def _without(name: str):
    def remove(target: Path) -> None:
        path = target / name
        shutil.rmtree(path) if path.is_dir() else path.unlink()

    return remove


def _ending_with_a_failed_run(target: Path) -> None:
    [entry] = run_log(target)
    failed = {**entry, "status": "failed", "commit": None, "error": "the session timed out"}
    with (target / "run-log.jsonl").open("a") as log:
        log.write(json.dumps(failed) + "\n")


def _by_two_librarians(target: Path) -> None:
    [entry] = run_log(target)
    with (target / "run-log.jsonl").open("a") as log:
        log.write(json.dumps({**entry, "librarian_version": BUILT_BY}) + "\n")


def _never_committed(target: Path) -> None:
    [entry] = run_log(target)
    failed = {**entry, "status": "failed", "commit": None, "error": "the session timed out"}
    (target / "run-log.jsonl").write_text(json.dumps(failed) + "\n")


NOT_COPIES = {
    "no wiki": (_without("wiki"), "has no wiki/"),
    "no run log": (_without("run-log.jsonl"), "has no run-log.jsonl"),
    "a replay that never committed a run": (_never_committed, "has no committed run"),
    "a replay that stopped on a failed run": (
        _ending_with_a_failed_run,
        "ends with a failed run: finish the replay with --resume first",
    ),
    "a replay by two librarian versions": (
        _by_two_librarians,
        "names more than one librarian version",
    ),
}
"""What isn't a copy of a finished replay's target: how to break one, and why it's refused."""


@pytest.mark.parametrize(("spoil", "reason"), NOT_COPIES.values(), ids=list(NOT_COPIES))
def test_dreaming_refuses_a_target_that_isnt_a_copy_of_a_finished_replays_target(
    data_root: Path, corpus: Path, capsys, spoil, reason: str
):
    target = copy_of(replayed(data_root, corpus))
    spoil(target)
    before = tree_state(target)

    assert dream(target, "--corpus", corpus) == 1

    assert reason in capsys.readouterr().err
    assert sessions(data_root, "dreaming") == []
    assert tree_state(target) == before


CUT_OR_CORPUS = {
    "a target with neither": (["TARGET"], "give --corpus DIR or --cut DIR"),
    "a corpus without a target": (["--corpus", "CORPUS"], "--corpus goes with a TARGET"),
}


@pytest.mark.parametrize(("args", "reason"), CUT_OR_CORPUS.values(), ids=list(CUT_OR_CORPUS))
def test_a_copy_is_dreamed_over_the_l0_it_was_built_over(
    data_root: Path, corpus: Path, capsys, args: list[str], reason: str
):
    target = copy_of(replayed(data_root, corpus))
    before = tree_state(target)
    named = {"TARGET": str(target), "CORPUS": str(corpus)}

    assert main(["librarian", "dream", *(named.get(arg, arg) for arg in args)]) == 1

    assert reason in capsys.readouterr().err
    assert sessions(data_root, "dreaming") == []
    assert tree_state(target) == before


# A copy of a cut's wiki

CAPTURED_AT = datetime(2026, 10, 14, 12, tzinfo=CHICAGO)
CUT_AS_OF = datetime.fromisoformat(AS_OF_UTC)
"""When the cut is taken: the same moment the corpus is replayed up to."""


def cut_of_a_live_wiki(data_root: Path) -> Cut:
    """A cut of a data root whose wiki a nightly run built from the wedding's mail, captured
    before the cut's as-of time."""
    root = DataRoot(data_root)
    for name, payload in MAIL.items():
        (root.inbox / name).parent.mkdir(parents=True, exist_ok=True)
        (root.inbox / name).write_bytes(payload)
    store = FilesystemL0(root.l0)
    root.catalog.parent.mkdir(parents=True, exist_ok=True)
    with open_catalog(root.catalog, store) as catalog:
        ingest(root.inbox, store, catalog, load_owner(), now=lambda: CAPTURED_AT)
    script(
        data_root,
        observation=[{"observe": True, "links": LINKS}],
        consolidation=[{"write": path, "text": text} for path, text in PAGES.items()],
    )
    assert main(["librarian", "run"]) == 0
    return take_cut(root, now=CUT_AS_OF)


def copy_of_the_cuts_wiki(cut: Cut, data_root: Path) -> Path:
    """A copy of the cut's wiki under the data root's replays, as one is made by hand."""
    assert cut.wiki is not None
    target = data_root / "replays" / "cut-dreamed"
    shutil.copytree(cut.wiki, target / "wiki")
    return target


def test_dreaming_a_copy_of_a_cuts_wiki_runs_over_the_cuts_l0_at_the_cuts_as_of_time(
    data_root: Path, capsys
):
    cut = cut_of_a_live_wiki(data_root)
    assert cut.manifest.wiki is not None
    live = fields(commit_messages(data_root)[0])
    live_parts = ("wiki", "wiki.git", "status")
    before = tree_state(cut.path), [tree_state(data_root / part) for part in live_parts]
    target = copy_of_the_cuts_wiki(cut, data_root)
    script(data_root, dreaming=[{"write": TOPIC, "text": CLOSED}, {"outcomes": [APPLIED]}])

    assert dream(target, "--cut", cut.path) == 0

    # The session saw the cut's L0, and was told the cut's as-of time.
    [session] = sessions(data_root, "dreaming")
    assert session["settings"]["permissions"]["additionalDirectories"] == [
        str(cut.l0),
        str(target / "wiki"),
    ]
    assert told_now(session) == "2026-10-14T20:00:00-05:00"
    assert [candidate["kind"] for candidate in session["candidates"]] == ["loop-aging"]
    # The copy starts from what the cut says of its wiki, which the dreaming commit carries.
    dreamed, start, _ = commit_messages(target)
    assert fields(start) == {
        "Cursor": live["Cursor"],
        "Librarian-Version": live["Librarian-Version"],
        "Copy-As-Of": AS_OF_UTC,
    }
    recorded = fields(dreamed)
    assert (recorded["Cursor"], recorded["Librarian-Version"]) == (
        live["Cursor"],
        live["Librarian-Version"],
    )
    assert (target / "wiki" / TOPIC).read_text() == CLOSED
    # The copy gets a run log, which holds its dreaming run alone.
    [entry] = run_log(target)
    assert (entry["run"], entry["status"], entry["as_of"]) == ("dreaming", "committed", AS_OF_UTC)
    assert entry["commit"] == git(target, "rev-parse", "HEAD").strip()
    assert entry["librarian_version"] == live["Librarian-Version"]
    assert entry["outcomes"] == {"applied": 1, "proposed": 0, "dismissed": 0}
    # The cut is untouched, and so is the live wiki, with its history and its status.
    after = tree_state(cut.path), [tree_state(data_root / part) for part in live_parts]
    assert after == before


INSIDE = Path(__file__).resolve().parents[3] / "evals" / "wikis" / "private-test-never-written"
"""A path inside the repository, which no test ever writes."""


def test_a_copy_of_a_cuts_wiki_is_refused_inside_the_repository(data_root: Path, capsys):
    cut = cut_of_a_live_wiki(data_root)

    assert dream(INSIDE, "--cut", cut.path) == 1

    assert f"({INSIDE}) is inside the repository" in capsys.readouterr().err
    assert sessions(data_root, "dreaming") == []
    assert not INSIDE.exists()


def test_a_copy_dreamed_as_of_one_cut_isnt_dreamed_again_as_of_another(data_root: Path, capsys):
    cut = cut_of_a_live_wiki(data_root)
    target = copy_of_the_cuts_wiki(cut, data_root)
    script(data_root, dreaming=[{"outcomes": [DISMISSED]}])
    assert dream(target, "--cut", cut.path) == 0
    later = take_cut(DataRoot(data_root), now=datetime(2026, 10, 20, 1, tzinfo=CUT_AS_OF.tzinfo))
    before = tree_state(target)

    assert dream(target, "--cut", later.path) == 1

    error = capsys.readouterr().err
    assert "2026-10-15T01:00:00+00:00" in error and "2026-10-20T01:00:00+00:00" in error
    assert len(sessions(data_root, "dreaming")) == 1
    assert tree_state(target) == before


def test_a_failed_dreaming_run_of_a_cuts_copy_is_reported_without_content(data_root: Path, capsys):
    """A cut's copy is the owner's data, and the session that dreams it reads counts and exit
    codes only: a run's error quotes pages, so it stays in the copy's run log."""
    cut = cut_of_a_live_wiki(data_root)
    target = copy_of_the_cuts_wiki(cut, data_root)
    undreamed = tree_state(target / "wiki")
    # It retitles Dave's page, which no outcome claims: the run fails, naming the page.
    retitled = PAGES[PERSON].replace("# David Kim", "# Dave Kim")
    script(data_root, dreaming=[{"write": PERSON, "text": retitled}, {"outcomes": [DISMISSED]}])
    capsys.readouterr()

    assert dream(target, "--cut", cut.path) == 1

    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err == (
        "pa: the run failed in its dreaming session and was discarded; "
        f"its error is in {target / 'run-log.jsonl'}\n"
    )
    [failed] = run_log(target)
    assert failed["status"] == "failed" and PERSON in failed["error"]
    assert tree_state(target / "wiki") == undreamed


def test_dreaming_a_copy_takes_a_corpus_or_a_cut_not_both(data_root: Path, corpus: Path, capsys):
    cut = cut_of_a_live_wiki(data_root)
    target = copy_of_the_cuts_wiki(cut, data_root)

    assert dream(target, "--cut", cut.path, "--corpus", corpus) == 1

    assert "give --corpus DIR or --cut DIR" in capsys.readouterr().err
    assert sessions(data_root, "dreaming") == []


def test_a_cut_without_a_wiki_has_no_copy_to_dream(data_root: Path, capsys):
    cut = take_cut(DataRoot(data_root), now=CUT_AS_OF)
    target = data_root / "replays" / "cut-dreamed"
    (target / "wiki").mkdir(parents=True)

    assert dream(target, "--cut", cut.path) == 1

    assert "holds no wiki" in capsys.readouterr().err
