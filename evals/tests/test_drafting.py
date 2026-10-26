"""Drafting the real-data eval set (`pa-eval draft`): a session reads a cut's L0 and drafts the
set for the owner to correct. Built here on a synthetic cut of the fixture corpus, with a fake
agent that replies with a scripted draft."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import ingest
from pa_core.owner import Owner
from pa_core.status import SourceStatus, Status, write_status
from pa_evals.cli import main
from pa_evals.drafting import ScriptedDrafter, draft_eval_set, revise_eval_set
from pa_evals.eval_set import load_eval_set
from pa_home.config import DataRoot
from pa_home.cut import Cut, take_cut
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"
EVAL_SET = FIXTURES / "eval_set.json"
EVAL_WIKI = FIXTURES / "eval-wiki"

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
OWNER_ENV = {
    "PA_OWNER_NAME": "Argus McNevans",
    "PA_OWNER_EMAILS": "argus@example.com",
    "PA_OWNER_OTHER_NAMES": "Gus",
    "PA_TIME_ZONE": "America/Chicago",
}

DEADLINE_EMAIL = "gmail_94f6b4cbc55a3b85"
SWIM_EVENT = "icloud_calendar_9d69b653f9a42b18"
SWIM_NOTE = "obsidian_0510599eb323eee0"
MISSING = "gmail_0000000000000000"


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


@pytest.fixture
def cut(data_root: DataRoot) -> Cut:
    """A cut of a data root holding the fixture corpus, with a wiki and a status file."""
    as_of = load_eval_set(EVAL_SET).as_of
    shutil.copytree(CORPUS, data_root.inbox)
    store = FilesystemL0(data_root.l0)
    captured = as_of - timedelta(days=1)
    with open_catalog(data_root.catalog, store) as catalog:
        ingest(data_root.inbox, store, catalog, OWNER, now=lambda: captured)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    shutil.copytree(EVAL_WIKI / "wiki", data_root.wiki, dirs_exist_ok=True)
    repository.commit("Observe a batch\n\nLibrarian-Version: fedcba987654\n")
    status = Status(sources={"gmail": SourceStatus(watermark=captured, cadence=timedelta(hours=1))})
    write_status(data_root.status, status, at=captured)
    # Taken as `pa cut` takes one: its as-of time in UTC.
    return take_cut(data_root, now=as_of.astimezone(UTC))


@pytest.fixture
def owner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in OWNER_ENV.items():
        monkeypatch.setenv(name, value)


def question(category: str, evidence: list[str], **extra: str) -> dict:
    return {
        "category": category,
        "question": f"A made-up {category} question?",
        "expected_answer": "A made-up answer.",
        "evidence": evidence,
        **extra,
    }


def reply_file(tmp_path: Path, questions: list[dict], *, name: str = "reply.txt") -> Path:
    """A scripted reply holding the draft, wrapped the way a session might wrap it."""
    path = tmp_path / name
    path.write_text(
        "Here is the draft.\n\n```json\n" + json.dumps({"questions": questions}) + "\n```\n"
    )
    return path


def draft(cut: Cut, out: Path, reply: Path, *extra: str) -> int:
    return main(
        [
            "draft",
            "--cut",
            str(cut.path),
            "--out",
            str(out),
            "--agent",
            "scripted",
            "--reply",
            str(reply),
            *extra,
        ]
    )


def test_a_draft_over_a_cut_loads_as_an_eval_set_with_opaque_ids_under_the_data_root(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys
):
    reply = reply_file(
        tmp_path,
        [
            question("single_fact_recall", [DEADLINE_EMAIL], id="hotel-block-deadline"),
            question("cross_channel_synthesis", [SWIM_EVENT, SWIM_NOTE]),
            question("abstention", []),
        ],
    )
    out = data_root.evals / "drafts" / cut.name / "draft-1.json"

    assert draft(cut, out, reply) == 0

    drafted = load_eval_set(out, cut=cut)
    assert [q.id for q in drafted.questions] == ["r01", "r02", "r03"]
    assert [q.evidence for q in drafted.questions] == [
        [DEADLINE_EMAIL],
        [SWIM_EVENT, SWIM_NOTE],
        [],
    ]
    assert drafted.as_of == cut.as_of
    assert drafted.owner.to_owner() == OWNER
    assert drafted.time_zone == "America/Chicago"
    printed = capsys.readouterr().out
    # What it prints says how much was drafted, never what.
    assert "made-up" not in printed
    assert f"draft: {out}" in printed


def test_a_draft_citing_an_episode_missing_from_the_cut_is_rejected(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys
):
    reply = reply_file(
        tmp_path,
        [
            question("single_fact_recall", [DEADLINE_EMAIL]),
            question("attribution", [MISSING, SWIM_NOTE]),
        ],
    )
    out = data_root.evals / "drafts" / cut.name / "draft-1.json"

    assert draft(cut, out, reply) == 1

    assert not out.exists()
    error = capsys.readouterr().err
    assert MISSING in error
    assert "made-up" not in error


def test_a_reply_that_isnt_a_draft_is_rejected_without_quoting_it(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys
):
    reply = reply_file(tmp_path, [question("made-up-category", [DEADLINE_EMAIL])])
    out = data_root.evals / "drafts" / "draft-1.json"

    assert draft(cut, out, reply) == 1

    assert not out.exists()
    error = capsys.readouterr().err
    assert "questions.0.category" in error
    assert "made-up" not in error


def test_a_second_pass_revises_the_draft_as_the_owner_left_it_by_their_notes(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None
):
    drafts = data_root.evals / "drafts" / cut.name
    first = drafts / "draft-1.json"
    reply = reply_file(
        tmp_path,
        [
            question("single_fact_recall", [DEADLINE_EMAIL]),
            question("attribution", [SWIM_NOTE]),
            question("abstention", []),
        ],
    )
    assert draft(cut, first, reply) == 0
    # The owner corrects an answer by hand, and writes their notes.
    edited = json.loads(first.read_text())
    edited["questions"][0]["expected_answer"] = "The owner's own answer."
    first.write_text(json.dumps(edited))
    notes = drafts / "notes.md"
    notes.write_text("Drop r02, and add a question about the swim lessons.\n")
    revised_reply = reply_file(
        tmp_path,
        [
            {**edited["questions"][0]},
            question("abstention", [], id="r03"),
            question("cross_channel_synthesis", [SWIM_EVENT, SWIM_NOTE]),
        ],
        name="revised.txt",
    )
    second = drafts / "draft-2.json"

    assert draft(cut, second, revised_reply, "--revise", str(first), "--notes", str(notes)) == 0

    revised = load_eval_set(second, cut=cut)
    assert [q.id for q in revised.questions] == ["r01", "r03", "r04"]
    assert revised.questions[0].expected_answer == "The owner's own answer."
    assert revised.questions[2].evidence == [SWIM_EVENT, SWIM_NOTE]
    assert revised.as_of == load_eval_set(first).as_of
    # The first draft is left as the owner left it.
    assert json.loads(first.read_text()) == edited


def test_a_second_pass_shows_the_session_the_draft_as_the_owner_left_it_and_their_notes(
    tmp_path: Path, cut: Cut, owner_env: None
):
    reply = reply_file(tmp_path, [question("single_fact_recall", [DEADLINE_EMAIL])])
    first = draft_eval_set(
        cut, ScriptedDrafter(reply.read_text()), owner=OWNER, time_zone="America/Chicago"
    )
    edited = first.model_copy(
        update={
            "questions": [
                first.questions[0].model_copy(update={"expected_answer": "The owner's answer."})
            ]
        }
    )
    revising = ScriptedDrafter(reply.read_text())

    revise_eval_set(cut, revising, previous=edited, notes="Ask it the other way round.")

    [request] = revising.requests
    assert "The owner's answer." in request.prompt
    assert "Ask it the other way round." in request.prompt
    assert request.as_of == cut.as_of


def test_the_drafting_prompt_gives_each_sources_watermark_at_the_cut(tmp_path: Path, cut: Cut):
    reply = reply_file(tmp_path, [question("abstention", [])])
    drafter = ScriptedDrafter(reply.read_text())

    draft_eval_set(cut, drafter, owner=OWNER, time_zone="America/Chicago", questions=30)

    [request] = drafter.requests
    assert "draft about 30 questions" in request.prompt
    assert "  - gmail: 2026-10-05T20:00:00-05:00" in request.prompt
    assert "  - icloud_calendar: never collected" in request.prompt
    assert "  - obsidian: never collected" in request.prompt


def test_a_second_pass_over_a_draft_the_owner_broke_is_refused_without_quoting_it(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys
):
    drafts = data_root.evals / "drafts"
    first = drafts / "draft-1.json"
    reply = reply_file(tmp_path, [question("single_fact_recall", [DEADLINE_EMAIL])])
    assert draft(cut, first, reply) == 0
    edited = json.loads(first.read_text())
    edited["questions"][0]["category"] = "made-up category"
    first.write_text(json.dumps(edited))
    notes = drafts / "notes.md"
    notes.write_text("Made-up notes.\n")

    result = draft(
        cut, drafts / "draft-2.json", reply, "--revise", str(first), "--notes", str(notes)
    )

    assert result == 1
    error = capsys.readouterr().err
    assert "questions.0.category" in error
    assert "made-up" not in error.lower()


def test_a_draft_is_never_written_over(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys
):
    reply = reply_file(tmp_path, [question("single_fact_recall", [DEADLINE_EMAIL])])
    out = data_root.evals / "draft.json"
    out.parent.mkdir(parents=True)
    out.write_text("the owner's edits\n")

    assert draft(cut, out, reply) == 1

    assert out.read_text() == "the owner's edits\n"
    assert "already exists" in capsys.readouterr().err


STAND_IN_CLAUDE = """\
import json, os, sys
from pathlib import Path
argv = sys.argv[1:]
settings = json.loads(Path(".claude/settings.json").read_text())
l0 = settings["permissions"]["additionalDirectories"][0]
Path(os.environ["SEEN"]).write_text(json.dumps({
    "argv": argv,
    "prompt": sys.stdin.read(),
    "environ": dict(os.environ),
    "claude_md": Path("CLAUDE.md").read_text(),
    "directories": settings["permissions"]["additionalDirectories"],
    "l0_files": sorted(str(p.relative_to(l0)) for p in Path(l0).rglob("*") if p.is_file()),
    "beside_l0": sorted(p.name for p in Path(l0).parent.iterdir()),
}))
draft = {"questions": [{"category": "single_fact_recall", "question": "When?",
                        "expected_answer": "Then.", "evidence": ["gmail_94f6b4cbc55a3b85"]}]}
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(draft), "total_cost_usd": 1.5, "num_turns": 40,
                  "duration_ms": 900000}))
"""


def test_the_drafting_session_runs_with_capture_off_and_without_the_wiki(
    tmp_path: Path,
    data_root: DataRoot,
    cut: Cut,
    owner_env: None,
    monkeypatch: pytest.MonkeyPatch,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(f"#!{sys.executable}\n{STAND_IN_CLAUDE}")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SEEN", str(tmp_path / "seen.json"))
    # Capture is on in the caller's environment, as it is in the VM's Remote Control unit.
    monkeypatch.setenv("PA_CAPTURE_EXCHANGES", "1")
    monkeypatch.setenv("PA_CAPTURE_INBOX", str(tmp_path / "inbox"))
    out = data_root.evals / "drafts" / cut.name / "draft-1.json"

    exit_code = main(
        [
            *("draft", "--cut", str(cut.path), "--out", str(out)),
            *("--agent", "claude", "--agent-model", "sonnet"),
        ]
    )

    assert exit_code == 0
    seen = json.loads((tmp_path / "seen.json").read_text())
    # Capture off: the session's environment has no capture settings.
    assert "PA_CAPTURE_EXCHANGES" not in seen["environ"]
    assert "PA_CAPTURE_INBOX" not in seen["environ"]
    # Without the wiki: the project gives L0 and scratch alone, and no wiki section; and L0 is a
    # copy of the cut's, with no wiki beside it.
    l0, _scratch = seen["directories"]
    assert not l0.startswith(str(cut.path))
    assert "Find your way with the wiki" not in seen["claude_md"]
    assert "wiki" not in seen["beside_l0"]
    assert seen["l0_files"] == sorted(
        str(p.relative_to(cut.l0)) for p in cut.l0.rglob("*") if p.is_file()
    )
    argv = seen["argv"]
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    # Asked at the cut's as-of time, in the owner's time zone, with the versioned prompt.
    assert "Tuesday, October 6, 2026, 20:00" in argv[argv.index("--append-system-prompt") + 1]
    assert seen["prompt"].startswith("# Draft your owner's eval set")
    assert "gmail, icloud_calendar, obsidian" in seen["prompt"]
    assert [q.id for q in load_eval_set(out, cut=cut).questions] == ["r01"]


INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-draft-test-never-written"


@pytest.mark.parametrize("which", ["--out", "--revise", "--notes"])
def test_drafts_and_notes_inside_the_repository_are_refused(
    tmp_path: Path, data_root: DataRoot, cut: Cut, owner_env: None, capsys, which: str
):
    reply = reply_file(tmp_path, [question("single_fact_recall", [DEADLINE_EMAIL])])
    private = data_root.evals / "drafts"
    out = INSIDE / "draft.json" if which == "--out" else private / "draft-2.json"
    revise = INSIDE / "draft-1.json" if which == "--revise" else private / "draft-1.json"
    notes = INSIDE / "notes.md" if which == "--notes" else private / "notes.md"

    result = draft(cut, out, reply, "--revise", str(revise), "--notes", str(notes))

    assert result == 1
    assert "is inside the repository" in capsys.readouterr().err
    assert not INSIDE.exists()
    assert not (private / "draft-2.json").exists()
