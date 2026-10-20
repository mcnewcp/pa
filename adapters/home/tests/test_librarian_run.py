"""`pa librarian run`: whole runs over a temporary data root, through a stand-in `claude`.

The stand-in plays the librarian's session. It records what it was given (argv, the prompt
and the episodes listed in it, its working directory, the CLAUDE.md there and the settings
passed to it) to `sessions.jsonl`, then follows the script for its phase in `script.json`:

- `{"observe": true}`: one observation per listed episode, in the day log for its day,
  linking `[[someone]]`, or the slugs `links` maps its subject to;
- `{"delete": <path in the wiki>}`;
- `{"write": <path in the wiki>, "text": ...}` and `{"append": ..., "text": ...}`;
- `{"symlink": <path in the wiki>, "to": <target>}` and `{"mkdir": <path in the wiki>}`;
- `{"sleep": <seconds>}`;
- `{"fail": <message>}`: report an error and exit 1.

With no script, observation observes and consolidation does nothing. Then it prints what
`claude -p --output-format json` prints. The tests assert on the wiki, the git history and the
cursor, and on what the session was given (`inputs` holds each fenced JSON block of its prompt).
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_home import librarian
from pa_home.cli import main
from pa_home.config import (
    LibrarianConfig,
    load_data_root,
    load_librarian_config,
    load_owner,
    load_time_zone,
)
from pa_home.librarian_project import LIBRARIAN_PROJECT

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Sent late on 30 Sep in Chicago, which is already 1 Oct in UTC.
HOTEL_EMAIL_ID = "gmail_9ff7394d0df8584d"


def email(
    *,
    message_id: str = "wedding-0001@example.net",
    labels: str = "Inbox",
    date: str = "Wed, 30 Sep 2026 21:30:00 -0500",
    sender: str = "David Kim <dkim@example.net>",
    subject: str = "Hotel block",
    body: str = "The hotel block closes on Oct 9.",
) -> bytes:
    return (
        f"X-Gmail-Labels: {labels}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        f"Date: {date}\r\n"
        f"From: {sender}\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        f"Subject: {subject}\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode()


def stand_in_claude(directory: Path) -> Path:
    script = directory / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import json, os, re, sys, time
            from pathlib import Path
            LOG = Path({str(directory / "sessions.jsonl")!r})
            SCRIPT = Path({str(directory / "script.json")!r})
            """
        )
        + textwrap.dedent(
            """\
            argv = sys.argv[1:]
            prompt = sys.stdin.read()
            settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
            wiki = Path(settings["permissions"]["additionalDirectories"][1])
            phase = re.search(r"^Phase: (\\w+)$", prompt, re.M)[1]
            listing = re.search(r"^```json\\n(.*?)^```$", prompt, re.M | re.S)
            episodes = json.loads(listing[1]) if listing else []
            inputs = [json.loads(block) for block in
                      re.findall(r"^```json\\n(.*?)^```$", prompt, re.M | re.S)]
            claude_md = Path("CLAUDE.md")
            with LOG.open("a") as log:
                log.write(json.dumps({
                    "phase": phase, "argv": argv, "prompt": prompt, "cwd": os.getcwd(),
                    "claude_md": claude_md.read_text() if claude_md.exists() else None,
                    "settings": settings, "episodes": episodes, "inputs": inputs,
                }) + "\\n")
            script = json.loads(SCRIPT.read_text()) if SCRIPT.exists() else {}

            def write(path, text, mode="w"):
                target = wiki / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open(mode) as file:
                    file.write(text)

            default = {"observation": [{"observe": True}]}
            for action in script.get(phase, default.get(phase, [])):
                if "observe" in action:
                    for episode in episodes:
                        slugs = action.get("links", {}).get(episode["subject"], ["someone"])
                        links = " and ".join(f"[[{slug}]]" for slug in slugs)
                        write(f"days/{episode['day']}.md",
                              f"- {episode['kind']} about {episode['subject']} with "
                              f"{links} [ep:{episode['id']}]\\n", "a")
                elif "delete" in action:
                    (wiki / action["delete"]).unlink()
                elif "write" in action:
                    write(action["write"], action["text"])
                elif "append" in action:
                    write(action["append"], action["text"], "a")
                elif "symlink" in action:
                    (wiki / action["symlink"]).parent.mkdir(parents=True, exist_ok=True)
                    (wiki / action["symlink"]).symlink_to(action["to"])
                elif "mkdir" in action:
                    (wiki / action["mkdir"]).mkdir(parents=True, exist_ok=True)
                elif "sleep" in action:
                    time.sleep(action["sleep"])
                elif "fail" in action:
                    print(json.dumps({"type": "result", "subtype": "error_during_execution",
                                      "is_error": True, "result": action["fail"]}))
                    sys.exit(1)
            print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": "Done.", "session_id": "session-1",
                              "total_cost_usd": 0.0421, "num_turns": 6,
                              "duration_ms": 18250}))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pa-data"
    monkeypatch.setenv("PA_DATA_DIR", str(root))
    monkeypatch.setenv("PA_OWNER_NAME", "Argus McNevans")
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com")
    monkeypatch.setenv("PA_OWNER_OTHER_NAMES", "Gus")
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_LIBRARIAN_COMMAND", str(stand_in_claude(tmp_path)))
    for var in (
        "PA_LIBRARIAN_MODEL",
        "PA_LIBRARIAN_EFFORT",
        "PA_LIBRARIAN_TIMEOUT_SECONDS",
        "PA_LIBRARIAN_PAGE_THRESHOLD",
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


def trailers(message: str) -> dict[str, str]:
    fields = {}
    for line in message.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.replace("-", "").isalnum():
            fields[key] = value
    return fields


def cursor(data_root: Path) -> str | None:
    return trailers(commit_messages(data_root)[0]).get("Cursor")


def wiki_files(data_root: Path) -> dict[str, str]:
    wiki = data_root / "wiki"
    return {
        p.relative_to(wiki).as_posix(): p.read_text()
        for p in sorted(wiki.rglob("*"))
        if p.is_file()
    }


NO_PAGES_INDEX = "# Index\n\nNo pages yet.\n"
"""The index the host generates for a wiki without pages."""


def test_a_run_observes_new_episodes_into_day_logs_and_commits_them(data_root: Path, capsys):
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    assert wiki_files(data_root) == {
        "days/2026-09-30.md": f"- email about Hotel block with [[someone]] [ep:{HOTEL_EMAIL_ID}]\n",
        "index.md": NO_PAGES_INDEX,
    }
    [message, *_] = commit_messages(data_root)
    fields = trailers(message)
    assert fields["Batch-Episodes"] == "1"
    captured_from = datetime.fromisoformat(fields["Batch-Captured-From"])
    captured_to = datetime.fromisoformat(fields["Batch-Captured-To"])
    assert captured_from == captured_to
    assert abs(datetime.now(UTC) - captured_to) < timedelta(minutes=1)  # ingested just now
    assert fields["Cursor"] == f"{captured_to.isoformat()} {HOTEL_EMAIL_ID}"
    assert fields["Model"] == "claude-opus-5-5"
    assert fields["Effort"] == "medium"
    assert len(fields["Librarian-Version"]) == 12
    assert fields["Observation-Cost-USD"] == "0.0421"
    assert fields["Observation-Turns"] == "6"
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""


def daily_note(captured_at: str, text: str, path: str = "Daily/2026-10-05.md") -> bytes:
    return f"Vault-Path: {path}\nCaptured-At: {captured_at}\n\n{text}\n".encode()


CHAT = Path(__file__).parents[3] / "core/tests/fixtures/assistant_chat/single_exchange.json"


def test_the_batch_follows_the_selection_rules_and_lists_each_episodes_day(data_root: Path):
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            "gmail/sale.eml": email(
                message_id="sale@shop.example",
                labels="Inbox,Category Promotions",
                subject="50% off",
            ),
            "gmail/chatter.eml": email(
                message_id="chatter@social.example",
                labels="Category Social",
                subject="New follower",
            ),
            "gmail/reminder.eml": email(
                message_id="reminder@clinic.example",
                labels="Inbox,Category Updates",
                sender="Birchwood Clinic <noreply@birchwood.example>",
                subject="Appointment reminder",
            ),
            "obsidian/first.md": daily_note("2026-10-06T01:00:00-05:00", "Swim: weekdays."),
            "obsidian/second.md": daily_note("2026-10-06T08:00:00-05:00", "Swim: Saturdays."),
            "assistant_chat/exchange.json": CHAT.read_bytes(),
        },
    )

    assert run_librarian() == 0

    [session] = sessions(data_root, "observation")
    listed = {e["subject"] or e["kind"]: e for e in session["episodes"]}
    assert set(listed) == {"Hotel block", "Appointment reminder", "note", "chat"}
    # Ordered by capture time: the note's capture header says when it was captured.
    assert session["episodes"][0]["kind"] == "note"

    hotel = listed["Hotel block"]
    assert hotel["id"] == HOTEL_EMAIL_ID
    assert Path(hotel["path"]).is_file()
    assert Path(hotel["path"]).is_relative_to(data_root / "l0")
    assert (hotel["source"], hotel["kind"]) == ("gmail", "email")
    assert hotel["day"] == "2026-09-30"  # 1 Oct in UTC, still 30 Sep in Chicago
    assert {"identifier": "dkim@example.net", "role": "sender", "name": "David Kim"} in hotel[
        "participants"
    ]
    assert "judge_relevance" not in hotel
    assert listed["Appointment reminder"]["judge_relevance"] is True

    note = listed["note"]
    assert note["day"] == "2026-10-05"  # the note's own date, whatever the time zone
    first = json.loads(Path(note["path"]).read_text())
    assert first["body"] == "Swim: Saturdays."
    assert len(note["supersedes"]) == 1

    chat = listed["chat"]
    assert chat["day"] == "2026-10-07"  # 01:12 UTC on 8 Oct
    assert chat["owner_turns"] == ["When are Theo and June's swim lessons?"]

    fields = trailers(commit_messages(data_root)[0])
    assert (fields["Batch-Episodes"], fields["Batch-Skipped"]) == ("4", "3")


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_the_session_is_told_now_and_has_only_the_file_tools_and_the_rendered_settings(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_LIBRARIAN_MAX_TURNS", "40")
    monkeypatch.setenv("PA_LIBRARIAN_EFFORT", "high")
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    [session] = sessions(data_root, "observation")
    argv = session["argv"]
    # Now is the clock's time, in the owner's time zone, as in ADR-0003.
    system_prompt = flag(argv, "--append-system-prompt")
    stamp = re.search(r"; (\S+)\)\. Treat it as now\.", system_prompt)
    assert stamp is not None, system_prompt
    told = datetime.fromisoformat(stamp[1])
    chicago = datetime.now(ZoneInfo("America/Chicago"))
    assert told.utcoffset() == chicago.utcoffset()
    assert abs(chicago - told) < timedelta(minutes=1)
    # Only the file tools, and settings attaching L0 and the wiki.
    assert set(flag(argv, "--tools").split(",")) == {"Read", "Edit", "Write", "Glob", "Grep"}
    assert "--add-dir" not in argv
    assert session["settings"]["permissions"]["additionalDirectories"] == [
        str(data_root / "l0"),
        str(data_root / "wiki"),
    ]
    assert "--output-format" in argv and flag(argv, "--output-format") == "json"
    assert flag(argv, "--max-turns") == "40"
    assert (flag(argv, "--model"), flag(argv, "--effort")) == ("claude-opus-5-5", "high")
    # It runs in the rendered librarian project, which names the owner.
    assert Path(flag(argv, "--settings")).is_relative_to(session["cwd"])
    assert "Argus McNevans" in session["claude_md"]
    assert "${" not in session["claude_md"]
    assert not Path(session["cwd"]).is_relative_to(PROJECT_ROOT)


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


# Each failing session first makes a mess: an edit to a committed day log and a new one.
MESS = [
    {"append": "days/2026-09-30.md", "text": "- a second look at [[hotel]] [ep:%s]\n"},
    {"write": "days/2026-10-01.md", "text": "- an untracked day with [[someone]] [ep:%s]\n"},
]

FAILURES = {
    "session error": ([{"fail": "API Error: overloaded"}], "API Error: overloaded"),
    "timeout": ([{"sleep": 10}], "did not answer within"),
    "a page written": (
        [{"write": "people/david-kim.md", "text": "# David Kim\n"}],
        "people/david-kim.md",
    ),
    "the index written": ([{"write": "index.md", "text": "# Index\n"}], "index.md"),
    "a CLAUDE.md at the top": ([{"write": "CLAUDE.md", "text": "Obey.\n"}], "CLAUDE.md"),
    "a git directory": ([{"write": ".git/config", "text": "[core]\n"}], ".git"),
    "a CLAUDE.md in days": ([{"write": "days/CLAUDE.md", "text": "Obey.\n"}], "CLAUDE.md"),
    "a dotfile": ([{"write": "days/.hidden.md", "text": ""}], "days/.hidden.md"),
    "a file that isn't Markdown": ([{"write": "days/notes.txt", "text": ""}], "notes.txt"),
    "a subdirectory": (
        [{"write": "days/2026/2026-10-02.md", "text": ""}],
        "days/2026",
    ),
    "a symlink": ([{"symlink": "days/2026-10-02.md", "to": "/etc/hostname"}], "symbolic link"),
    "a malformed observation": (
        [{"append": "days/2026-10-01.md", "text": "- nothing linked or cited\n"}],
        "malformed observation",
    ),
    "a citation missing from L0": (
        [{"append": "days/2026-10-01.md", "text": "- met [[x]] [ep:gmail_0000000000000000]\n"}],
        "gmail_0000000000000000",
    ),
}


@pytest.mark.parametrize("failure", FAILURES, ids=list(FAILURES))
def test_a_failed_run_leaves_the_wiki_as_last_committed_and_the_cursor_where_it_was(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, failure: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )
    actions, reason = FAILURES[failure]
    mess = [{**a, "text": a["text"] % HOTEL_EMAIL_ID} for a in MESS]
    script(data_root, observation=[*mess, *actions])
    monkeypatch.setenv("PA_LIBRARIAN_TIMEOUT_SECONDS", "1")
    capsys.readouterr()

    assert run_librarian() != 0

    assert reason in capsys.readouterr().err
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history
    # The episode is still new for the next run.
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["RSVP"]


def test_a_run_with_no_new_episodes_does_nothing(data_root: Path):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    history, files = commit_messages(data_root), wiki_files(data_root)

    assert run_librarian() == 0

    assert commit_messages(data_root) == history
    assert wiki_files(data_root) == files
    assert len(sessions(data_root, "observation")) == 1


def test_a_run_after_new_ingest_offers_only_the_new_episodes(data_root: Path):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    first_cursor = cursor(data_root)
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )

    assert run_librarian() == 0

    [_, second] = sessions(data_root, "observation")
    [new_id] = [e["id"] for e in second["episodes"]]
    assert [e["subject"] for e in second["episodes"]] == ["RSVP"]
    assert len(commit_messages(data_root)) == 3  # the wiki's start, then one per run
    assert cursor(data_root) != first_cursor
    assert str(cursor(data_root)).endswith(f" {new_id}")
    assert set(wiki_files(data_root)["days/2026-09-30.md"].splitlines()) == {
        f"- email about Hotel block with [[someone]] [ep:{HOTEL_EMAIL_ID}]",
        f"- email about RSVP with [[someone]] [ep:{new_id}]",
    }


def test_a_batch_of_nothing_worth_offering_moves_the_cursor_without_a_session(data_root: Path):
    ingest(data_root, {"gmail/sale.eml": email(labels="Category Promotions", subject="Sale")})

    assert run_librarian() == 0

    assert sessions(data_root) == []
    assert wiki_files(data_root) == {"index.md": NO_PAGES_INDEX}
    fields = trailers(commit_messages(data_root)[0])
    assert (fields["Batch-Episodes"], fields["Batch-Skipped"]) == ("0", "1")
    assert run_librarian() == 0
    assert len(commit_messages(data_root)) == 2  # the wiki's start, then the one run


def test_the_wikis_git_directory_is_beside_the_wiki_and_nothing_of_git_is_in_it(
    data_root: Path,
):
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    assert git(data_root, "rev-parse", "--is-bare-repository") in ("true\n", "false\n")
    wiki = data_root / "wiki"
    assert [p for p in wiki.rglob("*") if "git" in p.name.lower()] == []
    assert not (data_root / "wiki.git").resolve().is_relative_to(wiki.resolve())
    assert git(data_root, "ls-tree", "-r", "--name-only", "HEAD").split() == [
        "days/2026-09-30.md",
        "index.md",
    ]


def test_the_owners_time_zone_decides_each_episodes_day(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_TIME_ZONE", "Asia/Tokyo")
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    [episode] = sessions(data_root, "observation")[0]["episodes"]
    assert episode["day"] == "2026-10-01"  # 11:30 on 1 Oct in Tokyo
    assert list(wiki_files(data_root)) == ["days/2026-10-01.md", "index.md"]


@pytest.mark.parametrize("value", ["", "Mars/Olympus_Mons"])
def test_a_run_needs_the_owners_time_zone(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, value: str
):
    monkeypatch.setenv("PA_TIME_ZONE", value)
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() != 0

    assert "PA_TIME_ZONE" in capsys.readouterr().err
    assert sessions(data_root) == []
    assert not (data_root / "wiki.git").exists()


def test_the_librarian_version_changes_with_the_project_the_model_and_the_effort(
    data_root: Path, tmp_path: Path
):
    project = tmp_path / "librarian-project"
    shutil.copytree(LIBRARIAN_PROJECT, project)
    base = load_librarian_config()
    runs = 0

    def version(config: LibrarianConfig) -> str:
        nonlocal runs
        runs += 1
        ingest(data_root, {f"gmail/{runs}.eml": email(message_id=f"{runs}@example.net")})
        librarian.run(
            load_data_root(),
            owner=load_owner(),
            time_zone=load_time_zone(),
            config=config,
            project=project,
        )
        return trailers(commit_messages(data_root)[0])["Librarian-Version"]

    first = version(base)
    assert version(base) == first
    assert version(replace(base, model="claude-sonnet-5-5")) != first
    assert version(replace(base, effort="high")) != first
    with (project / "CLAUDE.md").open("a") as claude_md:
        claude_md.write("\nOne more rule.\n")
    assert version(base) != first


def test_what_an_interrupted_run_left_in_the_wiki_is_never_committed(data_root: Path):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    # A run killed mid-session leaves its writes behind.
    (data_root / "wiki" / "people").mkdir()
    (data_root / "wiki" / "people" / "mallory.md").write_text("# Mallory\n")
    ingest(data_root, {"gmail/rsvp.eml": email(message_id="rsvp@example.net", subject="RSVP")})

    assert run_librarian() == 0

    assert "people/mallory.md" not in git(data_root, "ls-tree", "-r", "--name-only", "HEAD")
    assert "people/mallory.md" not in wiki_files(data_root)


# Consolidation (#61)

# The slugs the scripted observation links, by subject.
WEDDING = {
    "Hotel block": ["david-kim", "wedding"],
    "RSVP": ["david-kim", "wedding"],
    "Menu": ["wedding"],
    "Gift": ["david-kim", "wedding", "kitchen"],
}


def wedding_mail(subject: str, date: str) -> dict[str, bytes]:
    message = email(message_id=f"{subject}@example.net", subject=subject, date=date)
    return {f"gmail/{subject}.eml": message}


def test_consolidation_gets_the_runs_new_observations_and_the_reference_counts(
    data_root: Path,
):
    script(data_root, observation=[{"observe": True, "links": WEDDING}])
    ingest(
        data_root,
        {
            **wedding_mail("Hotel block", "Wed, 30 Sep 2026 09:00:00 -0500"),
            **wedding_mail("RSVP", "Fri, 2 Oct 2026 09:00:00 -0500"),
            **wedding_mail("Menu", "Fri, 2 Oct 2026 17:00:00 -0500"),
        },
    )
    assert run_librarian() == 0
    ingest(data_root, wedding_mail("Gift", "Mon, 5 Oct 2026 09:00:00 -0500"))

    assert run_librarian() == 0

    assert [s["phase"] for s in sessions(data_root)] == [
        "observation",
        "consolidation",
        "observation",
        "consolidation",
    ]
    [gift] = sessions(data_root, "observation")[1]["episodes"]
    new_observations, counts = sessions(data_root, "consolidation")[1]["inputs"]
    assert new_observations == [
        {
            "day": "2026-10-05",
            "line": "- email about Gift with [[david-kim]] and [[wedding]] and [[kitchen]] "
            f"[ep:{gift['id']}]",
        }
    ]
    # Distinct days over all day logs: the two lines on 2 Oct count once.
    assert counts == [
        {"slug": "david-kim", "days": 3, "reaches_threshold": True},
        {"slug": "wedding", "days": 3, "reaches_threshold": True},
        {"slug": "kitchen", "days": 1, "reaches_threshold": False},
    ]
    fields = trailers(commit_messages(data_root)[0])
    assert fields["Consolidation-Cost-USD"] == "0.0421"
    assert fields["Consolidation-Turns"] == "6"
    assert fields["Consolidation-Session"] == "session-1"


@pytest.mark.parametrize(("threshold", "reached"), [(None, False), ("2", True)])
def test_the_page_threshold_is_configurable_and_starts_at_three(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, threshold: str | None, reached: bool
):
    if threshold is not None:
        monkeypatch.setenv("PA_LIBRARIAN_PAGE_THRESHOLD", threshold)
    script(data_root, observation=[{"observe": True, "links": WEDDING}])
    ingest(
        data_root,
        {
            **wedding_mail("Hotel block", "Wed, 30 Sep 2026 09:00:00 -0500"),
            **wedding_mail("RSVP", "Fri, 2 Oct 2026 09:00:00 -0500"),
        },
    )

    assert run_librarian() == 0

    [consolidation] = sessions(data_root, "consolidation")
    _, counts = consolidation["inputs"]
    assert counts == [
        {"slug": "david-kim", "days": 2, "reaches_threshold": reached},
        {"slug": "wedding", "days": 2, "reaches_threshold": reached},
    ]


def test_a_run_whose_observation_wrote_nothing_has_no_consolidation(data_root: Path):
    script(data_root, observation=[])
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    assert [s["phase"] for s in sessions(data_root)] == ["observation"]
    fields = trailers(commit_messages(data_root)[0])
    assert "Consolidation-Turns" not in fields
    assert fields["Batch-Episodes"] == "1"


def wedding_pages(hotel: str, rsvp: str) -> dict[str, str]:
    """A page of each type, in the wiki schema's formats, citing two episodes."""
    return {
        "people/david-kim.md": textwrap.dedent(
            f"""\
            ---
            type: person
            aliases: [Dave, David Kim]
            status: active
            summary: Old friend, getting married on Oct 24.
            updated: 2026-10-02
            emails: [dkim@example.net]
            ---
            # David Kim

            ## Current state
            Getting married on Oct 24; I'm doing a reading [ep:{rsvp}].

            ## Open loops
            - [ ] [[david-kim]] → me: send the reading text · promised 2026-10-02 [ep:{rsvp}]

            ## Notes
            """
        ),
        "orgs/north-shore-lodge.md": textwrap.dedent(
            """\
            ---
            type: organization
            aliases: []
            status: active
            summary: The wedding venue, in Duluth.
            updated: 2026-10-02
            domains: []
            ---
            # North Shore Lodge

            ## Current state

            ## Open loops

            ## Notes
            """
        ),
        "topics/wedding.md": textwrap.dedent(
            """\
            ---
            type: topic
            aliases: [Dave's wedding]
            status: active
            summary: Dave's wedding on Oct 24 at North Shore Lodge.
            updated: 2026-10-02
            ---
            # Wedding

            ## Current state
            RSVP sent and the hotel booked [ep:{hotel}] [ep:{rsvp}].

            ## Decisions
            - 2026-10-02 · Gift: decided the knife set (me, [[mara]]) [ep:{rsvp}]
            {superseded}
            - 2026-10-02 · Reading: proposed a poem ([[david-kim]]) [ep:{rsvp}]

            ## Open loops
            {closed}
            - [ ] [[north-shore-lodge]] → me: the room list · asked 2026-09-30 [ep:{hotel}]

            ## Notes
            The block closes on Oct 9 [ep:{hotel}].
            """
        ).format(
            hotel=hotel,
            rsvp=rsvp,
            superseded=f"- ~~2026-09-30 · Travel: decided drive up Saturday (me) [ep:{hotel}]~~ "
            f"superseded 2026-10-02 by drive up Friday [ep:{rsvp}]",
            closed=f"- [x] me → [[david-kim]]: RSVP · asked 2026-09-30 · due 2026-10-09 "
            f"[ep:{hotel}] · closed 2026-10-02 [ep:{rsvp}]",
        ),
    }


def observed_ids(data_root: Path) -> dict[str, str]:
    """The id of each episode observed so far, by subject."""
    return {
        e["subject"]: e["id"] for s in sessions(data_root, "observation") for e in s["episodes"]
    }


def test_consolidation_creates_and_edits_pages_and_the_host_regenerates_the_index(
    data_root: Path,
):
    script(data_root, observation=[{"observe": True, "links": WEDDING}])
    ingest(
        data_root,
        {
            **wedding_mail("Hotel block", "Wed, 30 Sep 2026 09:00:00 -0500"),
            **wedding_mail("RSVP", "Fri, 2 Oct 2026 09:00:00 -0500"),
        },
    )
    assert run_librarian() == 0
    ids = observed_ids(data_root)
    pages = wedding_pages(ids["Hotel block"], ids["RSVP"])
    ingest(data_root, wedding_mail("Gift", "Mon, 5 Oct 2026 09:00:00 -0500"))
    script(
        data_root,
        observation=[{"observe": True, "links": WEDDING}],
        consolidation=[{"write": path, "text": text} for path, text in pages.items()],
    )

    assert run_librarian() == 0

    files = wiki_files(data_root)
    assert {path: files[path] for path in pages} == pages
    assert files["index.md"] == textwrap.dedent(
        """\
        # Index

        ## People

        - [[david-kim]]: Old friend, getting married on Oct 24.

        ## Organizations

        - [[north-shore-lodge]]: The wedding venue, in Duluth.

        ## Topics

        - [[wedding]]: Dave's wedding on Oct 24 at North Shore Lodge.
        """
    )
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""

    # The next run edits a summary and merges the lodge away; the index follows.
    ingest(data_root, wedding_mail("Menu", "Tue, 6 Oct 2026 09:00:00 -0500"))
    edited = pages["topics/wedding.md"].replace("at North Shore Lodge", "in Duluth")
    script(
        data_root,
        observation=[{"observe": True, "links": WEDDING}],
        consolidation=[
            {"write": "topics/wedding.md", "text": edited},
            {"delete": "orgs/north-shore-lodge.md"},
        ],
    )

    assert run_librarian() == 0

    _, counts = sessions(data_root, "consolidation")[-1]["inputs"]
    assert counts[:2] == [
        {"slug": "wedding", "days": 4, "reaches_threshold": True, "page": "topics/wedding.md"},
        {"slug": "david-kim", "days": 3, "reaches_threshold": True, "page": "people/david-kim.md"},
    ]
    assert wiki_files(data_root)["index.md"] == textwrap.dedent(
        """\
        # Index

        ## People

        - [[david-kim]]: Old friend, getting married on Oct 24.

        ## Topics

        - [[wedding]]: Dave's wedding on Oct 24 in Duluth.
        """
    )


def pages_with(path: str, old: str, new: str) -> list[dict]:
    """Consolidation writing the wedding pages, with `old` replaced by `new` in one of them."""
    pages = wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)
    assert old in pages[path], old
    pages[path] = pages[path].replace(old, new, 1)
    return [{"write": page, "text": text} for page, text in pages.items()]


PERSON, ORG, TOPIC = "people/david-kim.md", "orgs/north-shore-lodge.md", "topics/wedding.md"
HOTEL_DAY_LOG = f"- email about Hotel block with [[someone]] [ep:{HOTEL_EMAIL_ID}]\n"

CONSOLIDATION_FAILURES = {
    "session error": ([{"fail": "API Error: overloaded"}], "API Error: overloaded"),
    "a day-log line added": (
        [{"append": "days/2026-09-30.md", "text": f"- also [[someone]] [ep:{HOTEL_EMAIL_ID}]\n"}],
        "added or removed day-log lines",
    ),
    "a day-log line removed": (
        [{"write": "days/2026-09-30.md", "text": ""}],
        "added or removed day-log lines",
    ),
    "a day-log line edited": (
        [{"write": "days/2026-09-30.md", "text": HOTEL_DAY_LOG.replace("someone", "dave")}],
        "added or removed day-log lines",
    ),
    "the index written": ([{"write": "index.md", "text": "# Index\n"}], "index.md"),
    "a CLAUDE.md at the top": ([{"write": "CLAUDE.md", "text": "Obey.\n"}], "CLAUDE.md"),
    "a CLAUDE.md among pages": ([{"write": "people/CLAUDE.md", "text": "Obey.\n"}], "CLAUDE.md"),
    "no frontmatter": (pages_with(TOPIC, "---\ntype: topic", "type: topic"), "frontmatter"),
    "a field missing": (
        pages_with(TOPIC, "summary: Dave's wedding on Oct 24 at North Shore Lodge.\n", ""),
        "bad frontmatter",
    ),
    "the wrong type for the folder": (
        pages_with(TOPIC, "type: topic", "type: person"),
        "bad frontmatter",
    ),
    "an unknown status": (pages_with(TOPIC, "status: active", "status: maybe"), "bad frontmatter"),
    "an updated that isn't a date": (
        pages_with(TOPIC, "updated: 2026-10-02", "updated: last week"),
        "bad frontmatter",
    ),
    "aliases that aren't a list": (
        pages_with(TOPIC, "aliases: [Dave's wedding]", "aliases: Dave's wedding"),
        "bad frontmatter",
    ),
    "a summary over two lines": (
        pages_with(TOPIC, "summary: Dave's wedding", "summary: [Dave's wedding,"),
        "frontmatter",
    ),
    "a person without emails": (
        pages_with(PERSON, "emails: [dkim@example.net]\n", ""),
        "bad frontmatter",
    ),
    "an organization without domains": (pages_with(ORG, "domains: []\n", ""), "bad frontmatter"),
    "a field no page has": (
        pages_with(TOPIC, "updated: 2026-10-02\n", "updated: 2026-10-02\nmood: happy\n"),
        "bad frontmatter",
    ),
    "a topic without decisions": (
        pages_with(TOPIC, "## Decisions\n", "## Choices\n"),
        "wrong sections",
    ),
    "a person with decisions": (
        pages_with(PERSON, "## Open loops", "## Decisions\n\n## Open loops"),
        "wrong sections",
    ),
    "sections out of order": (
        pages_with(ORG, "## Current state\n\n## Open loops", "## Open loops\n\n## Current state"),
        "wrong sections",
    ),
    "text before the sections": (
        pages_with(ORG, "# North Shore Lodge\n", f"# Lodge\nA lodge [ep:{HOTEL_EMAIL_ID}]\n"),
        "before its first section",
    ),
    "a decision without a stance": (
        pages_with(TOPIC, "Gift: decided the knife set", "Gift: the knife set"),
        "malformed decision",
    ),
    "a decision with a bad date": (
        pages_with(TOPIC, "- 2026-10-02 · Gift", "- 2026-13-02 · Gift"),
        "malformed decision",
    ),
    "a decision without its parties": (
        pages_with(TOPIC, "the knife set (me, [[mara]])", "the knife set"),
        "malformed decision",
    ),
    "a superseded decision that lost its first citation": (
        pages_with(TOPIC, f"(me) [ep:{HOTEL_EMAIL_ID}]~~", "(me)~~"),
        "malformed decision",
    ),
    "a loop without its parties": (
        pages_with(TOPIC, "- [ ] [[north-shore-lodge]] → me: ", "- [ ] "),
        "malformed loop",
    ),
    "a loop neither asked nor promised": (
        pages_with(TOPIC, "the room list · asked", "the room list · wanted"),
        "malformed loop",
    ),
    "a closed loop that lost its opening citation": (
        pages_with(
            TOPIC, f"due 2026-10-09 [ep:{HOTEL_EMAIL_ID}] · closed", "due 2026-10-09 · closed"
        ),
        "malformed loop",
    ),
    "a closed loop without its closing": (
        pages_with(TOPIC, f" · closed 2026-10-02 [ep:{HOTEL_EMAIL_ID}]", ""),
        "malformed loop",
    ),
    "an open loop marked closed": (
        pages_with(TOPIC, "- [ ] [[north-shore-lodge]]", "- [x] [[north-shore-lodge]]"),
        "malformed loop",
    ),
    "a line without a citation": (
        pages_with(TOPIC, f"The block closes on Oct 9 [ep:{HOTEL_EMAIL_ID}].", "The block closes."),
        "without a citation",
    ),
    "a citation missing from L0": (
        pages_with(TOPIC, f"Oct 9 [ep:{HOTEL_EMAIL_ID}]", "Oct 9 [ep:gmail_0000000000000000]"),
        "gmail_0000000000000000",
    ),
}


@pytest.mark.parametrize("failure", CONSOLIDATION_FAILURES, ids=list(CONSOLIDATION_FAILURES))
def test_a_consolidation_out_of_bounds_or_failing_lint_fails_the_run(
    data_root: Path, capsys, failure: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )
    actions, reason = CONSOLIDATION_FAILURES[failure]
    script(data_root, consolidation=actions)
    capsys.readouterr()

    assert run_librarian() != 0

    assert reason in capsys.readouterr().err
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history
    # The episode is still new for the next run.
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["RSVP"]


def test_the_wedding_pages_pass_lint(data_root: Path):
    """The control for the failures above: unchanged, the pages are committed."""
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )
    script(data_root, consolidation=pages_with(TOPIC, "", ""))

    assert run_librarian() == 0

    assert set(wiki_files(data_root)) >= {PERSON, ORG, TOPIC}
