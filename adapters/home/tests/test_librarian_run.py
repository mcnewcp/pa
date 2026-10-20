"""`pa librarian run`: whole runs over a temporary data root, through a stand-in `claude`.

The stand-in plays the librarian's session. It records what it was given (argv, the prompt,
the episodes listed in it, the identity table under its `## Identity table` heading, lint's
warnings under `## Lint warnings` and errors under `## Lint errors`, its working directory,
the CLAUDE.md there and the settings passed to it) to `sessions.jsonl`,
then follows the script for its phase in `script.json`:

- `{"observe": true}`: one observation per listed episode, in the day log for its day,
  linking `[[someone]]`, or the slugs `links` maps its subject to;
- `{"delete": <path in the wiki>}`;
- `{"write": <path in the wiki>, "text": ...}` and `{"append": ..., "text": ...}`;
- `{"symlink": <path in the wiki>, "to": <target>}` and `{"mkdir": <path in the wiki>}`;
- `{"sleep": <seconds>}`;
- `{"fail": <message>}`: report an error and exit 1.

An action with `"when": <subject>` is taken only by a session listing an episode with that
subject. A resumed session (`--resume`) follows `<phase>_resume` instead, such as
`observation_resume`.

With no script, observation observes and everything else does nothing. Then it prints what
`claude -p --output-format json` prints. The tests assert on the wiki, the git history and the
cursor, and on what the session was given (`inputs` holds each fenced JSON block of its prompt
before the identity table).
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
from pa_home.git_wiki import GitWikiRepository
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
            table = re.search(r"^## Identity table$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            identity = json.loads(table[1]) if table else None
            found = re.search(r"^## Lint warnings$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            warnings = json.loads(found[1]) if found else None
            found = re.search(r"^## Lint errors$.*?^```json\\n(.*?)^```$", prompt, re.M | re.S)
            errors = json.loads(found[1]) if found else None
            phase_inputs = prompt[:table.start()] if table else prompt
            inputs = [json.loads(block) for block in
                      re.findall(r"^```json\\n(.*?)^```$", phase_inputs, re.M | re.S)]
            claude_md = Path("CLAUDE.md")
            with LOG.open("a") as log:
                log.write(json.dumps({
                    "phase": phase, "argv": argv, "prompt": prompt, "cwd": os.getcwd(),
                    "claude_md": claude_md.read_text() if claude_md.exists() else None,
                    "settings": settings, "episodes": episodes, "identity": identity,
                    "warnings": warnings, "errors": errors, "inputs": inputs,
                }) + "\\n")
            script = json.loads(SCRIPT.read_text()) if SCRIPT.exists() else {}

            def write(path, text, mode="w"):
                target = wiki / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open(mode) as file:
                    file.write(text)

            default = {"observation": [{"observe": True}]}
            # A resumed session follows `<phase>_resume`; a new one, `<phase>`.
            key = f"{phase}_resume" if "--resume" in argv else phase
            for action in script.get(key, default.get(key, [])):
                if action.get("when") not in (None, *(e.get("subject") for e in episodes)):
                    continue
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
    "an address that isn't in L0": (
        pages_with(
            PERSON, "emails: [dkim@example.net]", "emails: [dkim@example.net, d@kim.example]"
        ),
        "d@kim.example, which isn't in L0",
    ),
    "a domain that isn't in L0": (
        pages_with(ORG, "domains: []", "domains: [northshorelodge.example]"),
        "northshorelodge.example, which isn't in L0",
    ),
    "an address on two pages": (
        [
            *pages_with(PERSON, "", ""),
            {
                "write": "people/dave.md",
                "text": wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)[PERSON].replace(
                    "[dkim@example.net]", "[DKim@example.net]"
                ),
            },
        ],
        "an address belongs on one page",
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


def test_a_pages_addresses_and_domains_may_come_from_any_mail_in_l0(data_root: Path):
    """The control for the identity errors: L0's metadata is every episode's participants,
    even mail never offered to the librarian."""
    promotion = email(
        message_id="offer@northshorelodge.example",
        labels="Inbox,Category Promotions",
        sender="North Shore Lodge <offers@mail.northshorelodge.example>",
        subject="Winter rates",
    )
    ingest(data_root, {"gmail/hotel.eml": email(), "gmail/offer.eml": promotion})
    pages = pages_with(ORG, "domains: []", "domains: [mail.northshorelodge.example]")
    script(data_root, consolidation=pages)

    assert run_librarian() == 0

    assert "domains: [mail.northshorelodge.example]" in wiki_files(data_root)[ORG]


def identity(data_root: Path) -> dict:
    """The identity table the run's (only) observation session was given."""
    [session] = sessions(data_root, "observation")
    return session["identity"]


def test_consolidation_gets_the_same_identity_table_as_observation(data_root: Path):
    ingest(
        data_root,
        {
            "gmail/personal.eml": email(),
            "gmail/work.eml": email(
                message_id="work@kimlaw.example",
                sender="David Kim <david.kim@kimlaw.example>",
                subject="Contract",
            ),
        },
    )

    assert run_librarian() == 0

    [consolidation] = sessions(data_root, "consolidation")
    assert consolidation["identity"] is not None
    assert consolidation["identity"] == identity(data_root)
    [david] = [p for p in consolidation["identity"]["people"] if p["slug"] == "david-kim"]
    assert david["addresses"] == ["david.kim@kimlaw.example", "dkim@example.net"]
    assert consolidation["identity"]["owner"]["slug"] == "me"


def test_the_session_gets_addresses_grouped_by_display_name_with_suggested_slugs(
    data_root: Path,
):
    ingest(
        data_root,
        {
            "gmail/personal.eml": email(),
            "gmail/work.eml": email(
                message_id="work@kimlaw.example",
                sender="David Kim <david.kim@kimlaw.example>",
                to="argus@example.com, =?utf-8?q?Zo=C3=AB?=  Brandt <zoe@example.org>",
                subject="Contract",
            ),
            "gmail/shouty.eml": email(
                message_id="shouty@example.net",
                sender="DAVID KIM <dk@example.org>",
                subject="Lunch",
            ),
            "gmail/bare.eml": email(
                message_id="bare@example.net",
                sender="pat.hughes@example.net",
                subject="Hello",
            ),
        },
    )

    assert run_librarian() == 0

    people = {person["slug"]: person for person in identity(data_root)["people"]}
    # Display names match whatever their case and spacing; the commonest spelling names them.
    assert people["david-kim"] == {
        "slug": "david-kim",
        "name": "David Kim",
        "addresses": ["david.kim@kimlaw.example", "dk@example.org", "dkim@example.net"],
        "organizations": ["kimlaw"],
    }
    assert people["zoe-brandt"]["name"] == "Zoë Brandt"
    assert people["zoe-brandt"]["addresses"] == ["zoe@example.org"]
    # An address with no display name is a group of its own, its slug read from the address.
    assert people["pat-hughes"] == {
        "slug": "pat-hughes",
        "name": None,
        "addresses": ["pat.hughes@example.net"],
        "organizations": [],
    }
    assert set(people) == {"david-kim", "zoe-brandt", "pat-hughes"}


def test_organizations_come_from_mail_domains_that_arent_webmail(data_root: Path):
    ingest(
        data_root,
        {
            "gmail/reminder.eml": email(
                message_id="reminder@birchwood.example",
                sender="Birchwood Clinic <appointments@birchwood.example>",
                to="Argus McNevans <argus@example.com>, Hannah Lee <hlee@mail.birchwood.example>",
                subject="Appointment reminder",
            ),
            "gmail/results.eml": email(
                message_id="results@birchwood.example",
                sender="Hannah Lee <hannah.lee@birchwood.example>",
                subject="Results",
            ),
            "gmail/gmail.eml": email(
                message_id="gmail@gmail.com",
                sender="Ana Ruiz <ana.ruiz@gmail.com>",
                subject="Coffee?",
            ),
            "gmail/uk.eml": email(
                message_id="quote@builders.co.uk",
                sender="Harrow Builders <quotes@harrow-builders.co.uk>",
                to="Argus McNevans <argus@example.com>, accounts@harrow-builders.co.uk",
                subject="Your quote",
            ),
            "gmail/survey.eml": email(
                message_id="survey@birchwood.example",
                sender="noreply@birchwood.example",
                subject="How did we do?",
            ),
            # Bulk mail is never offered, so its senders stay out of the table.
            "gmail/sale.eml": email(
                message_id="sale@shop.example",
                labels="Inbox,Category Promotions",
                sender="Shop <deals@shop.example>",
                subject="50% off",
            ),
        },
    )

    assert run_librarian() == 0

    table = identity(data_root)
    assert table["organizations"] == [
        {
            "slug": "birchwood",
            "name": None,
            "domains": ["birchwood.example", "mail.birchwood.example"],
            "addresses": [],
            "members": ["birchwood-clinic", "hannah-lee", "noreply-birchwood"],
        },
        # Named for its domain and writing only from it: the organization's own address.
        {
            "slug": "harrow-builders",
            "name": "Harrow Builders",
            "domains": ["harrow-builders.co.uk"],
            "addresses": ["quotes@harrow-builders.co.uk"],
            "members": ["accounts-harrow-builders"],
        },
    ]
    people = {person["slug"]: person for person in table["people"]}
    assert set(people) == {
        "birchwood-clinic",
        "hannah-lee",
        "noreply-birchwood",
        "ana-ruiz",
        "accounts-harrow-builders",
    }
    assert people["hannah-lee"]["organizations"] == ["birchwood"]
    assert people["birchwood-clinic"]["organizations"] == ["birchwood"]
    assert people["accounts-harrow-builders"]["organizations"] == ["harrow-builders"]
    assert people["ana-ruiz"]["organizations"] == []
    assert table["owner"]["organizations"] == []


def test_only_the_owners_configured_addresses_are_the_owners(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_OWNER_EMAILS", "argus@example.com, Argus@McNevans-Design.example")
    ingest(
        data_root,
        {
            "gmail/impostor.eml": email(
                message_id="impostor@gmail.com",
                sender="Argus McNevans <argus.mcnevans@gmail.com>",
                subject="New address",
            ),
            "gmail/nickname.eml": email(
                message_id="nickname@example.net",
                sender="gus <gus@lakestays-payments.example>",
                subject="Wire the deposit",
            ),
            "gmail/renamed.eml": email(
                message_id="renamed@example.com",
                labels="Sent",
                sender="Dave <argus@example.com>",
                to="David Kim <dkim@example.net>",
                subject="Re: Hotel block",
            ),
        },
    )

    assert run_librarian() == 0

    table = identity(data_root)
    assert table["owner"] == {
        "slug": "me",
        "name": "Argus McNevans",
        "other_names": ["Gus"],
        "addresses": ["argus@example.com", "argus@mcnevans-design.example"],
        "organizations": ["mcnevans-design"],
    }
    people = {person["slug"]: person for person in table["people"]}
    # Someone else using the owner's name or nickname is someone else.
    assert people["argus-mcnevans"]["addresses"] == ["argus.mcnevans@gmail.com"]
    assert people["argus-mcnevans"]["uses_owner_name"] is True
    assert people["gus"]["addresses"] == ["gus@lakestays-payments.example"]
    assert people["gus"]["uses_owner_name"] is True
    # The owner's address is the owner's whatever name it goes by; "Dave" is no one's alias.
    assert set(people) == {"argus-mcnevans", "gus", "david-kim"}
    assert "uses_owner_name" not in people["david-kim"]
    assert all("aliases" not in person for person in people.values())
    organizations = {org["slug"]: org for org in table["organizations"]}
    assert organizations["mcnevans-design"]["members"] == ["me"]
    assert organizations["mcnevans-design"]["domains"] == ["mcnevans-design.example"]
    assert table["forged"] == []


def test_mail_from_the_owners_address_without_the_sent_label_is_flagged_as_forged(
    data_root: Path,
):
    forged_mail = email(
        message_id="forged@examp1e.com",
        labels="Inbox,Important",
        sender="Argus McNevans <ARGUS@example.com>",
        to="Denise Hart <denise@greatplains.example>",
        subject="Disregard my last message",
    )
    sent_mail = email(
        message_id="sent@example.com",
        labels="Sent, Important",
        sender="Argus McNevans <argus@example.com>",
        to="Denise Hart <denise@greatplains.example>",
        subject="Claim",
    )
    ingest(data_root, {"gmail/forged.eml": forged_mail, "gmail/sent.eml": sent_mail})

    assert run_librarian() == 0

    [session] = sessions(data_root, "observation")
    forged_id = next(e["id"] for e in session["episodes"] if e["subject"].startswith("Disregard"))
    assert session["identity"]["forged"] == [{"episode": forged_id, "address": "argus@example.com"}]
    # The forged mail is still offered, and the session is told what the flag means.
    assert len(session["episodes"]) == 2
    assert "`forged`" in session["prompt"]


MALFORMED = "- nothing linked or cited\n"
"""A day-log line lint rejects."""


@pytest.mark.parametrize("phase", ["observation", "consolidation"])
def test_a_session_that_leaves_lint_errors_is_resumed_with_the_findings_and_fixes_them(
    data_root: Path, phase: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    pages = wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)
    broken = pages[TOPIC].replace("Gift: decided the knife set", "Gift: the knife set")
    if phase == "observation":
        script(
            data_root,
            observation=[{"observe": True}, {"append": "days/2026-09-30.md", "text": MALFORMED}],
            observation_resume=[{"write": "days/2026-09-30.md", "text": HOTEL_DAY_LOG}],
        )
        finding = "days/2026-09-30.md:2: malformed observation"
    else:
        script(
            data_root,
            observation=[{"observe": True}],
            consolidation=[{"write": TOPIC, "text": broken}],
            consolidation_resume=[{"write": TOPIC, "text": pages[TOPIC]}],
        )
        finding = f"{TOPIC}:"

    assert run_librarian() == 0

    started, resumed = sessions(data_root, phase)
    assert "--resume" not in started["argv"]
    assert flag(resumed["argv"], "--resume") == "session-1"
    assert finding in resumed["prompt"]
    files = wiki_files(data_root)
    assert files["days/2026-09-30.md"] == HOTEL_DAY_LOG
    if phase == "consolidation":
        assert "malformed decision" in resumed["prompt"]
        assert files[TOPIC] == pages[TOPIC]
    fields = trailers(commit_messages(data_root)[0])
    name = phase.capitalize()
    # A resumed session's cost and turns add up over its attempts.
    assert fields[f"{name}-Resumes"] == "1"
    assert fields[f"{name}-Cost-USD"] == "0.0842"
    assert fields[f"{name}-Turns"] == "12"


@pytest.mark.parametrize("phase", ["observation", "consolidation"])
def test_lint_errors_that_remain_after_two_resumes_discard_the_run(
    data_root: Path, capsys, phase: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )
    malformed = [{"append": "days/2026-10-02.md", "text": MALFORMED}]
    broken = pages_with(TOPIC, "Gift: decided the knife set", "Gift: the knife set")
    # Each resume makes it worse, with a file lint rejects.
    worse = {
        "observation": {"append": "days/2026-10-03.md", "text": MALFORMED},
        "consolidation": {"write": "topics/worse.md", "text": "No frontmatter.\n"},
    }[phase]
    script(
        data_root,
        observation=[{"observe": True}, *(malformed if phase == "observation" else [])],
        consolidation=broken if phase == "consolidation" else [],
        **{f"{phase}_resume": [worse]},
    )
    capsys.readouterr()

    assert run_librarian() != 0

    attempts = sessions(data_root, phase)[-3:]
    assert ["--resume" in s["argv"] for s in attempts] == [False, True, True]
    # The first run's two sessions, then this run's: no third resume.
    assert len(sessions(data_root)) == 2 + (3 if phase == "observation" else 4)
    error = capsys.readouterr().err
    assert "lint errors after 2 resumes" in error
    assert worse.get("append", worse.get("write")) in error
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["RSVP"]


def days_ago(days: int) -> str:
    """The date `days` days before today in the owner's time zone; runs are told the clock."""
    return (datetime.now(ZoneInfo("America/Chicago")).date() - timedelta(days=days)).isoformat()


def current_wedding_pages() -> dict[str, str]:
    """The wedding pages, citing the hotel email, with their open loops opened yesterday."""
    pages = wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)
    pages[PERSON] = pages[PERSON].replace("promised 2026-10-02", f"promised {days_ago(1)}")
    pages[TOPIC] = pages[TOPIC].replace(
        "the room list · asked 2026-09-30", f"the room list · asked {days_ago(1)}"
    )
    return pages


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
    Waiting on a quote [ep:{HOTEL_EMAIL_ID}].

    ## Decisions

    ## Open loops

    ## Notes
    """
)
"""A topic page nothing links to."""


def _edit(path: str, old: str, new: str):
    def edit(pages: dict[str, str]) -> None:
        assert old in pages[path], old
        pages[path] = pages[path].replace(old, new, 1)

    return edit


WARNINGS = {
    "an orphan page": (
        lambda pages: pages.update({"topics/kitchen.md": KITCHEN}),
        "topics/kitchen.md: an orphan page",
        {},
    ),
    "a loop past due": (
        _edit(
            TOPIC,
            f"asked {days_ago(1)} [ep:",
            f"asked {days_ago(5)} · due {days_ago(2)} [ep:",
        ),
        f"{TOPIC}:20: a loop past due: due {days_ago(2)}, 2 days ago",
        {},
    ),
    "a loop open more than 30 days": (
        _edit(PERSON, f"promised {days_ago(1)}", f"promised {days_ago(31)}"),
        f"{PERSON}:15: a loop open 31 days",
        {},
    ),
    "a loop open longer than configured": (
        _edit(PERSON, f"promised {days_ago(1)}", f"promised {days_ago(11)}"),
        f"{PERSON}:15: a loop open 11 days",
        {"PA_LIBRARIAN_LOOP_AGE_DAYS": "10"},
    ),
    "a shared alias": (
        _edit(TOPIC, "aliases: [Dave's wedding]", "aliases: [Dave's wedding, dave]"),
        f"{TOPIC}: a duplicate candidate: it shares the alias dave with {PERSON}",
        {},
    ),
    "near-identical slugs": (
        lambda pages: pages.update({"topics/weddings.md": KITCHEN}),
        f"topics/weddings.md: a duplicate candidate: its slug is nearly {TOPIC}'s",
        {},
    ),
    "a slug over the threshold with no page": (
        lambda pages: pages.pop(TOPIC),
        "`wedding` is linked on 3 days, reaching the page threshold of 3, but has no page",
        {},
    ),
    "a needs-lookup marker": (
        _edit(TOPIC, "The block closes on Oct 9", "[needs lookup: Lodge check-in time] Oct 9"),
        f"{TOPIC}:23: needs lookup: Lodge check-in time",
        {},
    ),
}


def run_with_wedding_pages(data_root: Path, edit=None) -> None:
    """One run over three days of wedding mail, consolidation writing the wedding pages
    (changed by `edit`, if given)."""
    ingest(
        data_root,
        {
            "gmail/hotel.eml": email(),
            **wedding_mail("RSVP", "Fri, 2 Oct 2026 09:00:00 -0500"),
            **wedding_mail("Menu", "Mon, 5 Oct 2026 09:00:00 -0500"),
        },
    )
    pages = current_wedding_pages()
    if edit is not None:
        edit(pages)
    script(
        data_root,
        observation=[{"observe": True, "links": WEDDING}],
        consolidation=[{"write": path, "text": text} for path, text in pages.items()],
    )
    assert run_librarian() == 0


def test_a_run_without_lint_warnings_says_so_in_its_commit(data_root: Path):
    run_with_wedding_pages(data_root)

    [message, *_] = commit_messages(data_root)
    assert trailers(message)["Lint-Warnings"] == "0"
    assert "Lint warnings" not in message


@pytest.mark.parametrize("warning", WARNINGS, ids=list(WARNINGS))
def test_lint_warnings_dont_block_the_run_and_are_recorded_in_its_commit(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, warning: str
):
    edit, expected, environment = WARNINGS[warning]
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    run_with_wedding_pages(data_root, edit)

    [message, *_] = commit_messages(data_root)
    _, warnings, fields = message.split("\n\n")
    assert warnings.startswith("Lint warnings:\n- ")
    assert expected in warnings
    count = len(warnings.splitlines()) - 1
    assert trailers(fields)["Lint-Warnings"] == str(count)
    assert f"{count} lint warnings" in capsys.readouterr().out
    # The warnings don't hide the cursor from the next run.
    ingest(data_root, wedding_mail("Gift", "Tue, 6 Oct 2026 09:00:00 -0500"))
    script(data_root)
    assert run_librarian() == 0
    assert [e["subject"] for e in sessions(data_root, "observation")[-1]["episodes"]] == ["Gift"]


def test_lint_warnings_are_handed_to_the_next_consolidation(data_root: Path):
    run_with_wedding_pages(data_root, WARNINGS["an orphan page"][0])
    [first] = sessions(data_root, "consolidation")
    ingest(data_root, wedding_mail("Toast", "Tue, 6 Oct 2026 09:00:00 -0500"))
    script(data_root)

    assert run_librarian() == 0

    [observation] = sessions(data_root, "observation")[1:]
    [consolidation] = sessions(data_root, "consolidation")[1:]
    assert observation["warnings"] is None
    # The open warnings as consolidation starts: before the first run's pages, `wedding` had
    # reached the threshold with no page; the kitchen page it wrote is an orphan.
    assert first["warnings"] == [
        {
            "path": "days/2026-10-05.md",
            "message": "`wedding` is linked on 3 days, reaching the page threshold of 3, "
            "but has no page",
        }
    ]
    assert consolidation["warnings"] == [
        {
            "path": "topics/kitchen.md",
            "message": "an orphan page: nothing in the wiki links [[kitchen]]",
        }
    ]
    assert "only on the warnings about pages you touch" in consolidation["prompt"]


def test_after_a_merge_links_to_an_alias_are_rewritten_to_its_pages_slug(data_root: Path):
    # The first run gives Dave a page of his own, linked from the day log.
    ingest(data_root, {"gmail/hotel.eml": email()})
    pages = current_wedding_pages()
    script(
        data_root,
        observation=[{"observe": True, "links": {"Hotel block": ["dave", "wedding"]}}],
        consolidation=[{"write": "people/dave.md", "text": pages[PERSON]}],
    )
    assert run_librarian() == 0
    assert "[[dave]]" in wiki_files(data_root)["index.md"]
    # The next merges it into david-kim, which takes "Dave" as an alias; a page still links
    # [[dave]], and so do the day logs.
    ingest(data_root, wedding_mail("RSVP", "Fri, 2 Oct 2026 09:00:00 -0500"))
    topic = pages[TOPIC].replace("The block closes", "[[dave]] says the block closes")
    script(
        data_root,
        observation=[{"observe": True, "links": {"RSVP": ["dave"]}}],
        consolidation=[
            {"delete": "people/dave.md"},
            {"write": PERSON, "text": pages[PERSON]},
            {"write": ORG, "text": pages[ORG]},
            {"write": TOPIC, "text": topic},
        ],
    )

    assert run_librarian() == 0

    files = wiki_files(data_root)
    assert files["days/2026-09-30.md"] == (
        f"- email about Hotel block with [[david-kim]] and [[wedding]] [ep:{HOTEL_EMAIL_ID}]\n"
    )
    [rsvp] = sessions(data_root, "observation")[-1]["episodes"]
    assert files["days/2026-10-02.md"] == (
        f"- email about RSVP with [[david-kim]] [ep:{rsvp['id']}]\n"
    )
    assert "[[david-kim]] says the block closes" in files[TOPIC]
    # A page's own slug and other pages' links stay as they are.
    assert files[PERSON] == pages[PERSON]
    assert "[[north-shore-lodge]] → me" in files[TOPIC]
    assert "[[dave]]" not in files["index.md"]
    assert git(data_root, f"--work-tree={data_root / 'wiki'}", "status", "--porcelain") == ""


# Lint errors already in the last commit

BROKEN_TOPIC = wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)[TOPIC].replace(
    "Gift: decided the knife set", "Gift: the knife set"
)
"""The wedding topic with a malformed decision."""


def commit_by_hand(data_root: Path, files: dict[str, str]) -> None:
    """Commits `files` into the wiki under the last run's message, so the cursor stays: as a
    seeded wiki would be, or one a rule tightened since its last run now rejects."""
    repository = GitWikiRepository.open(data_root / "wiki", data_root / "wiki.git")
    for path, text in files.items():
        (data_root / "wiki" / path).parent.mkdir(parents=True, exist_ok=True)
        (data_root / "wiki" / path).write_text(text)
    repository.commit(repository.last_commit_message() or "")


NEW_EPISODES = {
    "new observations": (
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
        [{"observe": True}],
    ),
    "no new observations": (
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
        [],
    ),
    "nothing offered": (
        {
            "gmail/sale.eml": email(
                message_id="sale-0003@example.net", labels="Category Promotions", subject="Sale"
            )
        },
        [{"observe": True}],
    ),
}


@pytest.mark.parametrize("new", NEW_EPISODES, ids=list(NEW_EPISODES))
def test_page_errors_already_committed_are_handed_to_consolidation_to_fix(
    data_root: Path, new: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    commit_by_hand(data_root, {TOPIC: BROKEN_TOPIC})
    payloads, observation = NEW_EPISODES[new]
    ingest(data_root, payloads)
    fixed = wedding_pages(HOTEL_EMAIL_ID, HOTEL_EMAIL_ID)[TOPIC]
    script(data_root, observation=observation, consolidation=[{"write": TOPIC, "text": fixed}])

    assert run_librarian() == 0

    # Observation can't touch pages, so it's never resumed over one.
    assert all("--resume" not in s["argv"] for s in sessions(data_root, "observation"))
    consolidation = sessions(data_root, "consolidation")[-1]
    assert "--resume" not in consolidation["argv"]
    [error] = consolidation["errors"]
    assert error["path"] == TOPIC and "malformed decision" in error["message"]
    assert wiki_files(data_root)[TOPIC] == fixed
    assert "lint errors" not in commit_messages(data_root)[0]


def test_a_run_without_errors_already_committed_hands_consolidation_none(data_root: Path):
    ingest(data_root, {"gmail/hotel.eml": email()})

    assert run_librarian() == 0

    [consolidation] = sessions(data_root, "consolidation")
    assert consolidation["errors"] is None
    assert "## Lint errors" not in consolidation["prompt"]


ALREADY_COMMITTED = {
    "a page error consolidation doesn't fix": ({TOPIC: BROKEN_TOPIC}, 3),
    "a file outside the layout": ({"notes.txt": "Remember the milk.\n"}, 1),
}


@pytest.mark.parametrize("committed", ALREADY_COMMITTED, ids=list(ALREADY_COMMITTED))
def test_a_run_failing_only_on_errors_already_committed_says_so(
    data_root: Path, capsys, committed: str
):
    ingest(data_root, {"gmail/hotel.eml": email()})
    assert run_librarian() == 0
    files, consolidations = ALREADY_COMMITTED[committed]
    commit_by_hand(data_root, files)
    before, history = tree_state(data_root / "wiki"), commit_messages(data_root)
    ingest(
        data_root,
        {"gmail/rsvp.eml": email(message_id="rsvp-0002@example.net", subject="RSVP")},
    )
    script(data_root)
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert "all of them already in the wiki's last commit" in error
    assert next(iter(files)) in error
    # Observation is never resumed over them; consolidation only over the pages it can fix.
    assert len(sessions(data_root, "observation")) == 2
    assert len(sessions(data_root, "consolidation")[1:]) == consolidations
    # Nothing with errors is committed, even errors that were there before.
    assert tree_state(data_root / "wiki") == before
    assert commit_messages(data_root) == history


def test_a_run_failing_on_its_own_errors_doesnt_blame_the_last_commit(data_root: Path, capsys):
    ingest(data_root, {"gmail/hotel.eml": email()})
    script(data_root, consolidation=pages_with(TOPIC, "Gift: decided", "Gift:"))
    capsys.readouterr()

    assert run_librarian() != 0

    error = capsys.readouterr().err
    assert "malformed decision" in error
    assert "already in the wiki's last commit" not in error


# Replay (#64)

# Three weeks of wedding mail, Monday to Monday in Chicago, with nothing in the week of 12 Oct.
REPLAYED = {
    **wedding_mail("Hotel block", "Wed, 30 Sep 2026 09:00:00 -0500"),
    **wedding_mail("RSVP", "Wed, 7 Oct 2026 09:00:00 -0500"),
    **wedding_mail("Menu", "Tue, 20 Oct 2026 09:00:00 -0500"),
}


def replay(target: Path, *args: str) -> int:
    return main(["librarian", "replay", str(target), *args])


def replay_git(target: Path, *args: str) -> str:
    return subprocess.run(
        ["git", f"--git-dir={target / 'wiki.git'}", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def replay_messages(target: Path) -> list[str]:
    """Every commit's message in the replayed wiki, newest first."""
    log = replay_git(target, "log", "--format=%B%x00")
    return [m.strip() for m in log.split("\0") if m.strip()]


def told_now(session: dict) -> str:
    """The time the session was told is now, as it was written."""
    system_prompt = flag(session["argv"], "--append-system-prompt")
    stamp = re.search(r"; (\S+)\)\. Treat it as now\.", system_prompt)
    assert stamp is not None, system_prompt
    return stamp[1]


def observed_subjects(data_root: Path) -> list[list[str]]:
    """The subjects each observation session was given, session by session."""
    return [[e["subject"] for e in s["episodes"]] for s in sessions(data_root, "observation")]


def test_a_replay_runs_a_batch_per_week_of_occurrence_into_a_fresh_wiki(
    data_root: Path, tmp_path: Path
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"

    assert replay(target) == 0

    # The week of 12 Oct has no episodes, so no run.
    assert observed_subjects(data_root) == [["Hotel block"], ["RSVP"], ["Menu"]]
    # Each batch is told its end as now: the Monday after its week, in the owner's time zone.
    assert [told_now(s) for s in sessions(data_root, "observation")] == [
        "2026-10-05T00:00:00-05:00",
        "2026-10-12T00:00:00-05:00",
        "2026-10-26T00:00:00-05:00",
    ]
    # Each batch is a full run: observation, consolidation, and a commit.
    assert len(sessions(data_root, "consolidation")) == 3
    [*runs, start] = replay_messages(target)
    assert start == "Start the wiki"
    assert [
        (trailers(m)["Batch-Occurred-From"], trailers(m)["Batch-Occurred-To"])
        for m in reversed(runs)
    ] == [
        ("2026-09-28T05:00:00+00:00", "2026-10-05T05:00:00+00:00"),
        ("2026-10-05T05:00:00+00:00", "2026-10-12T05:00:00+00:00"),
        ("2026-10-19T05:00:00+00:00", "2026-10-26T05:00:00+00:00"),
    ]
    assert sorted(replay_git(target, "ls-tree", "-r", "--name-only", "HEAD").split()) == [
        "days/2026-09-30.md",
        "days/2026-10-07.md",
        "days/2026-10-20.md",
        "index.md",
    ]
    # The data root's own wiki is untouched.
    assert not (data_root / "wiki").exists()


def run_log(target: Path) -> list[dict]:
    return [json.loads(line) for line in (target / "run-log.jsonl").read_text().splitlines()]


def test_the_run_log_records_each_runs_batch_sessions_cost_and_the_librarian_version(
    data_root: Path, tmp_path: Path
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"

    assert replay(target) == 0

    first, *_ = entries = run_log(target)
    version = trailers(replay_messages(target)[0])["Librarian-Version"]
    run = {"phase": "observation", "session_id": "session-1", "cost_usd": 0.0421, "turns": 6}
    assert first == {
        "status": "committed",
        "commit": first["commit"],
        "occurred_from": "2026-09-28T05:00:00+00:00",
        "occurred_to": "2026-10-05T05:00:00+00:00",
        "episodes": 1,
        "skipped": 0,
        "librarian_version": version,
        "model": "claude-opus-5-5",
        "effort": "medium",
        "sessions": [run, {**run, "phase": "consolidation"}],
        "cost_usd": 0.0842,
        "error": None,
    }
    assert [e["occurred_from"][:10] for e in entries] == ["2026-09-28", "2026-10-05", "2026-10-19"]
    assert [e["commit"] for e in reversed(entries)] == replay_git(
        target, "log", "--format=%H", "-3"
    ).split()


def test_a_failed_replay_resumes_from_its_last_committed_batch(
    data_root: Path, tmp_path: Path, capsys
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"
    script(data_root, observation=[{"observe": True}, {"fail": "API Error", "when": "RSVP"}])

    assert replay(target) != 0

    assert "--resume" in capsys.readouterr().err
    [committed, failed] = run_log(target)
    assert committed["status"] == "committed"
    assert (failed["status"], failed["commit"], failed["sessions"]) == ("failed", None, [])
    assert failed["occurred_from"] == "2026-10-05T05:00:00+00:00"
    assert "API Error" in failed["error"]
    # The wiki is as of the first batch.
    assert len(replay_messages(target)) == 2
    assert sorted(p.name for p in (target / "wiki").rglob("*.md")) == ["2026-09-30.md", "index.md"]

    script(data_root)
    assert replay(target, "--resume") == 0

    assert observed_subjects(data_root) == [["Hotel block"], ["RSVP"], ["RSVP"], ["Menu"]]
    assert [e["status"] for e in run_log(target)] == [
        "committed",
        "failed",
        "committed",
        "committed",
    ]
    assert len(replay_messages(target)) == 4
    assert sorted(replay_git(target, "ls-tree", "-r", "--name-only", "HEAD").split()) == [
        "days/2026-09-30.md",
        "days/2026-10-07.md",
        "days/2026-10-20.md",
        "index.md",
    ]
    # A finished replay has nothing left to resume.
    assert replay(target, "--resume") == 0
    assert len(replay_messages(target)) == 4
    assert len(sessions(data_root, "observation")) == 4


def test_a_replay_starts_only_in_a_fresh_wiki_and_resumes_only_its_own_librarians_replay(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"
    script(data_root, observation=[{"observe": True}, {"fail": "API Error", "when": "Menu"}])
    assert replay(target) != 0
    history = replay_messages(target)
    script(data_root)
    capsys.readouterr()

    assert replay(target) != 0
    assert "isn't empty" in capsys.readouterr().err
    monkeypatch.setenv("PA_LIBRARIAN_EFFORT", "high")
    assert replay(target, "--resume") != 0
    assert "librarian version" in capsys.readouterr().err
    assert replay(tmp_path / "elsewhere", "--resume") != 0
    assert "no replay to resume" in capsys.readouterr().err
    # A production wiki isn't a replay's.
    assert run_librarian() == 0
    assert replay(data_root, "--resume") != 0
    assert "isn't a replay's" in capsys.readouterr().err

    assert replay_messages(target) == history
    assert len(sessions(data_root, "observation")) == 4  # three replayed, one production run


def test_the_batch_length_is_a_parameter(data_root: Path, tmp_path: Path):
    ingest(data_root, REPLAYED)

    assert replay(tmp_path / "rebuilt", "--batch-days", "14") == 0

    assert observed_subjects(data_root) == [["Hotel block", "RSVP"], ["Menu"]]
    assert [told_now(s) for s in sessions(data_root, "observation")] == [
        "2026-10-12T00:00:00-05:00",
        "2026-10-26T00:00:00-05:00",
    ]


def test_a_replay_up_to_an_as_of_time_ends_there_and_its_last_batch_takes_the_rest(
    data_root: Path, tmp_path: Path
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"

    assert replay(target, "--as-of", "2026-10-14T20:00:00") == 0

    # The menu mail, from after the as-of time, goes in the last batch, which ends at it.
    assert observed_subjects(data_root) == [["Hotel block"], ["RSVP"], ["Menu"]]
    assert [told_now(s) for s in sessions(data_root, "observation")] == [
        "2026-10-05T00:00:00-05:00",
        "2026-10-12T00:00:00-05:00",
        "2026-10-14T20:00:00-05:00",
    ]
    last = run_log(target)[-1]
    assert (last["occurred_from"], last["occurred_to"]) == (
        "2026-10-12T05:00:00+00:00",
        "2026-10-15T01:00:00+00:00",
    )


def test_a_replay_over_a_corpus_ingests_it_into_a_throwaway_data_root(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    mail, notes = tmp_path / "corpus" / "mail", tmp_path / "corpus" / "notes"
    for directory, payloads in (
        (mail, REPLAYED),
        (notes, {"obsidian/day.md": daily_note("2026-10-06T07:00:00-05:00", "Swim.")}),
    ):
        for name, payload in payloads.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(payload)
    monkeypatch.delenv("PA_DATA_DIR")
    target = tmp_path / "eval-wiki"

    assert (
        replay(
            target,
            "--corpus",
            str(mail),
            "--corpus",
            str(notes),
            "--as-of",
            "2026-10-21T20:00:00-05:00",
        )
        == 0
    )

    assert observed_subjects(data_root) == [["Hotel block"], [None, "RSVP"], ["Menu"]]
    [_, second, _] = sessions(data_root, "observation")
    note = second["episodes"][0]
    assert note["day"] == "2026-10-05"
    l0 = Path(note["path"]).parents[3]
    assert not l0.exists()  # thrown away
    assert not data_root.exists()
    # Mail was captured at the as-of time, as in an eval; the note when its header says.
    first = trailers(replay_messages(target)[-2])
    assert first["Batch-Captured-From"] == "2026-10-22T01:00:00+00:00"
    assert mail.is_dir() and (mail / "gmail" / "Menu.eml").is_file()  # the corpus is untouched


def test_a_rebuilt_wiki_moved_into_the_data_root_carries_on_where_the_replay_stopped(
    data_root: Path, tmp_path: Path
):
    ingest(data_root, REPLAYED)
    target = tmp_path / "rebuilt"
    assert replay(target) == 0
    for name in ("wiki", "wiki.git"):
        (target / name).rename(data_root / name)

    assert run_librarian() == 0
    assert len(sessions(data_root, "observation")) == 3  # nothing new since the replay
    ingest(data_root, wedding_mail("Gift", "Mon, 5 Oct 2026 09:00:00 -0500"))
    assert run_librarian() == 0

    assert observed_subjects(data_root)[-1] == ["Gift"]
