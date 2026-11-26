"""Reproductions: a diagnosis leaves fictional payloads, wiki pages, and eval questions that
fail the way the case did (`pa-eval diagnose`), and they reach a development session only with
the findings, when both pass every rule (`pa-eval release`).

Most tests make a diagnosis whose output holds a reproduction for its first finding, then run
the release. The clean reproduction is about a marmalade order from a larder. The case's people
and sentences stand in for real ones; a test plants one of them in the reproduction, and the
release is refused. The last tests run the session itself, as a stand-in `claude` (conftest's
`claude_stand_in`), which is resumed on a reproduction that isn't in form.

A run of words that is also in the repository passes the check, and so does a dotted name the
repository holds. This file is in the repository. So the case's sentences are written backwards
here (`forwards` turns them round), and a domain that must be refused is put together from its
parts."""

from __future__ import annotations

import base64
import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import ingest
from pa_core.owner import Owner
from pa_evals.cli import main
from pa_evals.eval_set import load_eval_set
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0

NAME = "dreamed-drop"
"""The diagnosis the `desk` (conftest) makes and releases."""


def forwards(backwards: str) -> str:
    """The words of `backwards` in the order the case holds them."""
    return " ".join(reversed(backwards.split()))


# What stands in for real content: a mail and two pages that the case holds.

BODY_LINE = forwards("wall left the along cracked kiln big The")
PAGE_LINE = forwards("bucket glaze blue the behind key studio spare the Keeps")
CASE_MAIL = f"""\
Message-ID: <kiln-0001@mail.pottery.example>
Date: Tue, 22 Sep 2026 09:15:00 -0500
From: Ottoline Varga <ovarga@pottery.example>
To: Argus McNevans <argus@example.com>
Cc: Billie <billie@pottery.example>
Subject: The firing
Content-Type: text/plain; charset="utf-8"

{BODY_LINE}, so every piece waits for the small one.
"""
CASE_PERSON = f"""\
---
type: person
aliases: [Mina]
emails: [billie@pottery.example]
status: active
summary: Runs the front desk.
updated: 2026-09-22
---
# Wilhelmina Stroud

## Current state
{PAGE_LINE}. [ep:gmail_0000000000000000]

## Open loops

## Notes
"""
CASE_ORGANIZATION = """\
---
type: organization
aliases: []
domains: [pottery.example]
status: active
summary: Where the classes are.
updated: 2026-09-22
---
# Quince

## Current state

## Open loops

## Notes
"""


@pytest.fixture
def also_in_the_case(also_in_the_case):
    also_in_the_case.payloads["gmail/kiln.eml"] = CASE_MAIL
    also_in_the_case.wiki["people/wilhelmina-stroud.md"] = CASE_PERSON
    also_in_the_case.wiki["orgs/quince.md"] = CASE_ORGANIZATION
    # A topic, and someone who is linked and has no page yet.
    also_in_the_case.wiki["topics/heron.md"] = "# Heron\n"
    also_in_the_case.wiki["days/2026-09-22.md"] = (
        "- [[fennimore]] signed up for [[heron]] [ep:gmail_0000000000000000]\n"
    )
    return also_in_the_case


OWNER = {
    "name": "Argus McNevans",
    "email_addresses": ["argus@example.com"],
    "other_names": ["Gus"],
}
"""The synthetic corpus's fictional owner, whose every reproduction is."""

ORDER = """\
Message-ID: <order-1@mail.larder.example>
Date: Mon, 21 Sep 2026 10:05:00 -0500
From: Pru Tanager <pru@larder.example>
To: Argus McNevans <argus@example.com>
Subject: Your marmalade order
Content-Type: text/plain; charset="utf-8"

The marmalade order ships on Friday, in two boxes.
"""
NOTE = """\
Vault-Path: Daily/2026-09-21.md
Captured-At: 2026-09-21T21:00:00-05:00

## Pantry

Ordered marmalade from the larder. Pru says it ships Friday.
"""
PICKUP = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Larder//Reproduction//EN
BEGIN:VEVENT
UID:pickup-1@larder.example
SEQUENCE:0
DTSTAMP:20260921T151000Z
DTSTART;TZID=America/Chicago:20260926T100000
DTEND;TZID=America/Chicago:20260926T103000
SUMMARY:Pick up the marmalade
ORGANIZER;CN=Argus McNevans:mailto:argus@example.com
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
PERSON = """\
---
type: person
aliases: []
emails: [pru@larder.example]
status: active
summary: Runs the larder the marmalade comes from.
updated: 2026-09-21
---
# Pru Tanager

## Current state
Wrote that the marmalade order ships on a Friday. {ep:gmail/order.eml}

## Open loops

## Notes
"""
DAY = "- [[pru-tanager]] wrote that my marmalade order ships Friday {ep:gmail/order.eml}\n"
EXPECTATION = (
    "The assistant should say the marmalade ships Friday and cite the order; it says it "
    "doesn't know.\n"
)
QUESTIONS: dict[str, Any] = {
    "as_of": "2026-09-22T20:00:00-05:00",
    "owner": OWNER,
    "time_zone": "America/Chicago",
    "questions": [
        {
            "id": "marmalade-ships",
            "category": "single_fact_recall",
            "question": "When does my marmalade order ship?",
            "expected_answer": "Friday.",
            "evidence": ["gmail/order.eml"],
        },
        {
            "id": "marmalade-pickup",
            "category": "cross_channel_synthesis",
            "question": "What have I planned around the marmalade?",
            "expected_answer": "It ships Friday, and the pickup is Saturday at 10.",
            "evidence": ["obsidian/2026-09-21.md", "icloud_calendar/pickup.ics"],
        },
    ],
}


CLEAN_PATHS = (
    "expectation.md",
    "questions.json",
    "payloads/gmail/order.eml",
    "payloads/obsidian/2026-09-21.md",
    "payloads/icloud_calendar/pickup.ics",
    "wiki/people/pru-tanager.md",
    "wiki/days/2026-09-21.md",
)
"""The clean reproduction's files."""


@pytest.fixture
def reproduction() -> dict[str, str]:
    """A clean reproduction's files, by path, as a session leaves them."""
    return {
        "expectation.md": EXPECTATION,
        "questions.json": json.dumps(QUESTIONS, indent=2),
        "payloads/gmail/order.eml": ORDER,
        "payloads/obsidian/2026-09-21.md": NOTE,
        "payloads/icloud_calendar/pickup.ics": PICKUP,
        "wiki/people/pru-tanager.md": PERSON,
        "wiki/days/2026-09-21.md": DAY,
    }


@pytest.fixture
def leave(leavings, well_formed) -> Callable[..., Path]:
    """Makes what a scripted diagnostician leaves: a private analysis, findings whose first
    names a reproduction, and that reproduction's `files`."""

    def leave(files: dict[str, str]) -> Path:
        well_formed["findings"][0].update(reproduction="f1", no_reproduction=None)
        left = leavings(well_formed)
        for path, text in files.items():
            target = left / "reproductions" / "f1" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return left

    return leave


def files_under(top: Path) -> list[str]:
    return sorted(str(path.relative_to(top)) for path in top.rglob("*") if path.is_file())


def ingested(payloads: Path, tmp_path: Path) -> dict[str, str]:
    """The episode id each subject or note's first line ingests to, by ingest's own rules, from
    a copy of the payloads in `payloads`."""
    data_root = DataRoot(tmp_path / "ingested")
    for payload in payloads.rglob("*"):
        if payload.is_file():
            target = data_root.inbox / payload.relative_to(payloads)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.read_bytes())
    store = FilesystemL0(data_root.l0)
    owner = Owner(name=OWNER["name"], email_addresses=tuple(OWNER["email_addresses"]))
    with open_catalog(data_root.catalog, store) as catalog:
        summary = ingest(data_root.inbox, store, catalog, owner)
    assert (summary.quarantined, summary.left) == (0, [])
    return {
        envelope.subject or envelope.body.splitlines()[0]: envelope.episode_id
        for envelope in store.envelopes()
    }


def test_a_clean_reproduction_is_released_beside_the_findings_resolved_and_untested(
    tmp_path: Path, desk, leave, reproduction, well_formed
):
    desk.diagnose(leave(reproduction))

    ran = desk.release()

    assert ran.code == 0
    released = desk.released / "reproductions" / "f1"
    assert files_under(desk.released) == [
        "findings.json",
        "release.json",
        "reproductions/f1/eval_set.json",
        "reproductions/f1/expectation.md",
        "reproductions/f1/payloads/gmail/order.eml",
        "reproductions/f1/payloads/icloud_calendar/pickup.ics",
        "reproductions/f1/payloads/obsidian/2026-09-21.md",
        "reproductions/f1/reproduction.json",
        "reproductions/f1/wiki/days/2026-09-21.md",
        "reproductions/f1/wiki/index.md",
        "reproductions/f1/wiki/people/pru-tanager.md",
    ]
    assert json.loads((desk.released / "findings.json").read_text()) == well_formed
    # The payloads and the expectation are released as they were left.
    assert (released / "payloads" / "gmail" / "order.eml").read_text() == ORDER
    assert (released / "payloads" / "icloud_calendar" / "pickup.ics").read_text() == PICKUP
    assert (released / "expectation.md").read_text() == EXPECTATION
    # Each question's evidence is the episode its payload ingests to.
    ids = ingested(released / "payloads", tmp_path)
    assert len(ids) == 3
    eval_set = json.loads((released / "eval_set.json").read_text())
    assert [question["evidence"] for question in eval_set["questions"]] == [
        [ids["Your marmalade order"]],
        [ids["## Pantry"], ids["Pick up the marmalade"]],
    ]
    assert eval_set == {
        **QUESTIONS,
        "questions": [
            {**asked, "evidence": question["evidence"], "as_of": None, "tier": None}
            for asked, question in zip(QUESTIONS["questions"], eval_set["questions"], strict=True)
        ],
        "adversarial": [],
    }
    # So is each citation in the wiki, and the wiki has its index.
    cited = f"[ep:{ids['Your marmalade order']}]"
    assert (released / "wiki" / "days" / "2026-09-21.md").read_text() == DAY.replace(
        "{ep:gmail/order.eml}", cited
    )
    assert (released / "wiki" / "people" / "pru-tanager.md").read_text() == PERSON.replace(
        "{ep:gmail/order.eml}", cited
    )
    assert "[[pru-tanager]]" in (released / "wiki" / "index.md").read_text()
    # It is marked untested, and the release is on record with its counts and with the episode
    # each payload ingests to, which is what its questions and its wiki were resolved to.
    assert json.loads((released / "reproduction.json").read_text()) == {
        "finding": "f1",
        "tested": False,
        "payloads": 3,
        "wiki_files": 2,
        "questions": 2,
        "episodes": {
            "gmail/order.eml": ids["Your marmalade order"],
            "icloud_calendar/pickup.ics": ids["Pick up the marmalade"],
            "obsidian/2026-09-21.md": ids["## Pantry"],
        },
    }
    record = json.loads((desk.released / "release.json").read_text())
    assert (record["reproductions"], record["payloads"]) == (1, 3)
    assert "reproductions: 1, untested\n" in ran.out
    assert "payloads: 3\n" in ran.out
    # What the session left stays as it left it, under the data root.
    assert files_under(desk.private / "output" / "reproductions") == sorted(
        f"f1/{path}" for path in reproduction
    )


def test_a_release_without_a_reproduction_says_so(desk, leavings):
    desk.diagnose(leavings())

    ran = desk.release()

    assert ran.code == 0
    assert not (desk.released / "reproductions").exists()
    record = json.loads((desk.released / "release.json").read_text())
    assert (record["reproductions"], record["payloads"]) == (0, 0)
    assert "reproductions: 0\n" in ran.out
    assert "payloads: 0\n" in ran.out


def test_a_released_reproduction_runs_through_the_existing_commands_as_it_is(
    tmp_path: Path, desk, leave, reproduction, pa_eval
):
    desk.diagnose(leave(reproduction))
    assert desk.release().code == 0
    released = desk.released / "reproductions" / "f1"

    # Its questions load as an eval set.
    eval_set = load_eval_set(released / "eval_set.json")
    [ships, pickup] = eval_set.questions
    # And `pa-eval run` ingests its payloads and asks them, with an answer that cites the
    # evidence the release resolved.
    answers, verdicts = tmp_path / "answers.json", tmp_path / "verdicts.json"
    answers.write_text(
        json.dumps({ships.id: f"Friday [ep:{ships.evidence[0]}].", pickup.id: "I don't know."})
    )
    verdicts.write_text(
        json.dumps(
            {
                ships.id: {
                    "correct": True,
                    "reasoning": "Same day.",
                    "relevant_citations": ships.evidence,
                },
                pickup.id: {"correct": False, "reasoning": "No plan.", "relevant_citations": []},
            }
        )
    )
    ran = pa_eval(
        *("run", "--corpus", released / "payloads", "--eval-set", released / "eval_set.json"),
        *("--runs-dir", tmp_path / "runs", "--agent", "scripted", "--answers", answers),
        *("--judge", "scripted", "--verdicts", verdicts),
    )

    assert ran.code == 0
    [run_dir] = (tmp_path / "runs").iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    assert results["ingest"] == {"ingested": 3, "unchanged": 0, "quarantined": 0}
    by_id = {question["id"]: question for question in results["questions"]}
    assert by_id["marmalade-ships"]["unknown_citations"] == []
    assert by_id["marmalade-ships"]["cited"] == ships.evidence
    assert results["overall"]["evidence_recall"] == 0.5
    assert results["overall"]["citation_validity"] == 1.0


# After an edit. A development session copies a released reproduction, renames or trims what
# reads like a real matter, and resolves it again (`pa-eval reproduction resolve`), since an
# edited payload is another episode. The command reads only what was released, so what it
# prints may say what it found.


@pytest.fixture
def copied(tmp_path: Path, desk, leave, reproduction) -> Path:
    """A development session's copy of the clean reproduction, as it was released."""
    desk.diagnose(leave(reproduction))
    assert desk.release().code == 0
    return shutil.copytree(desk.released / "reproductions" / "f1", tmp_path / "copied")


@pytest.fixture
def resolve(capsys: pytest.CaptureFixture[str]) -> Callable[[Path], tuple[int, str, str]]:
    """Runs `pa-eval reproduction resolve` over a directory: its exit code, and what it
    printed."""

    def run(directory: Path) -> tuple[int, str, str]:
        capsys.readouterr()
        code = main(["reproduction", "resolve", str(directory)])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return run


def test_a_reproduction_edited_after_its_release_is_resolved_again(
    tmp_path: Path, copied: Path, resolve
):
    before = ingested(copied / "payloads", tmp_path / "before")
    (copied / "payloads" / "gmail" / "order.eml").write_text(
        ORDER.replace("order-1@", "jam-1@").replace("two boxes", "three crates")
    )
    (copied / "payloads" / "obsidian" / "2026-09-21.md").write_text(
        NOTE.replace("Pru says it ships Friday", "It ships at the end of the week")
    )

    code, out, err = resolve(copied)

    assert (code, err) == (0, "")
    after = ingested(copied / "payloads", tmp_path / "after")
    assert after["Your marmalade order"] != before["Your marmalade order"]
    assert after["## Pantry"] != before["## Pantry"]
    assert after["Pick up the marmalade"] == before["Pick up the marmalade"]
    # The questions name the episodes the payloads ingest to now, and so does the wiki.
    eval_set = load_eval_set(copied / "eval_set.json")
    assert [question.evidence for question in eval_set.questions] == [
        [after["Your marmalade order"]],
        [after["## Pantry"], after["Pick up the marmalade"]],
    ]
    cited = f"[ep:{after['Your marmalade order']}]"
    assert (copied / "wiki" / "days" / "2026-09-21.md").read_text() == DAY.replace(
        "{ep:gmail/order.eml}", cited
    )
    assert (copied / "wiki" / "people" / "pru-tanager.md").read_text() == PERSON.replace(
        "{ep:gmail/order.eml}", cited
    )
    assert json.loads((copied / "reproduction.json").read_text()) == {
        "finding": "f1",
        "tested": False,
        "payloads": 3,
        "wiki_files": 2,
        "questions": 2,
        "episodes": {
            "gmail/order.eml": after["Your marmalade order"],
            "icloud_calendar/pickup.ics": after["Pick up the marmalade"],
            "obsidian/2026-09-21.md": after["## Pantry"],
        },
    }
    assert out.splitlines() == [
        "reproduction: f1, resolved again",
        "payloads: 3 (2 with a new episode id)",
        "questions: 2",
        "wiki files: 2",
    ]


def files_in(top: Path) -> dict[str, bytes]:
    return {path: (top / path).read_bytes() for path in files_under(top)}


def test_resolving_a_reproduction_nobody_edited_changes_nothing(copied: Path, resolve):
    released = files_in(copied)

    code, out, err = resolve(copied)

    assert (code, err) == (0, "")
    assert files_in(copied) == released
    assert "payloads: 3 (0 with a new episode id)\n" in out


def test_a_payload_whose_file_was_renamed_is_still_the_episode_it_was(
    tmp_path: Path, copied: Path, resolve
):
    released = files_in(copied)
    (copied / "payloads" / "gmail" / "order.eml").rename(copied / "payloads" / "gmail" / "jam.eml")

    code, _, err = resolve(copied)

    assert (code, err) == (0, "")
    # Nothing ingests to another episode, so the questions and the wiki stand as they were.
    for path in ("eval_set.json", "wiki/days/2026-09-21.md", "wiki/people/pru-tanager.md"):
        assert (copied / path).read_bytes() == released[path]
    ids = ingested(copied / "payloads", tmp_path)
    record = json.loads((copied / "reproduction.json").read_text())
    assert record["episodes"] == {
        "gmail/jam.eml": ids["Your marmalade order"],
        "icloud_calendar/pickup.ics": ids["Pick up the marmalade"],
        "obsidian/2026-09-21.md": ids["## Pantry"],
    }


def test_a_trimmed_reproduction_is_resolved_with_what_it_still_holds(copied: Path, resolve):
    (copied / "payloads" / "icloud_calendar" / "pickup.ics").unlink()
    (copied / "payloads" / "obsidian" / "2026-09-21.md").unlink()
    eval_set = json.loads((copied / "eval_set.json").read_text())
    eval_set["questions"] = eval_set["questions"][:1]
    (copied / "eval_set.json").write_text(json.dumps(eval_set))
    (copied / "wiki" / "people" / "pru-tanager.md").unlink()

    code, out, err = resolve(copied)

    assert (code, err) == (0, "")
    record = json.loads((copied / "reproduction.json").read_text())
    assert (record["payloads"], record["questions"], record["wiki_files"]) == (1, 1, 1)
    assert list(record["episodes"]) == ["gmail/order.eml"]
    assert load_eval_set(copied / "eval_set.json").questions[0].evidence == [
        record["episodes"]["gmail/order.eml"]
    ]
    # The index is the wiki's as it is now.
    assert "pru-tanager" not in (copied / "wiki" / "index.md").read_text()
    assert out.splitlines()[1:] == [
        "payloads: 1 (0 with a new episode id)",
        "questions: 1",
        "wiki files: 1",
    ]


def test_a_question_left_naming_a_payload_that_was_removed_is_refused(copied: Path, resolve):
    (copied / "payloads" / "icloud_calendar" / "pickup.ics").unlink()
    edited = files_in(copied)

    code, out, err = resolve(copied)

    assert (code, out) == (1, "")
    assert err.splitlines() == [
        "eval_set.json: questions.1.evidence: names an episode that none of the payloads is: "
        "its payload was removed, or renamed and edited at once",
        f"pa-eval: {copied} isn't resolved again, and is left as it was (1 problem)",
    ]
    assert files_in(copied) == edited


def test_a_wiki_left_citing_an_episode_that_is_no_payload_is_refused(copied: Path, resolve):
    day = copied / "wiki" / "days" / "2026-09-21.md"
    day.write_text(day.read_text() + "- [[pru-tanager]] sent a second jar [ep:gmail_abcdef]\n")
    edited = files_in(copied)

    code, out, err = resolve(copied)

    assert (code, out) == (1, "")
    assert err.splitlines() == [
        "wiki/days/2026-09-21.md:2: cites an episode that none of the payloads is: its payload "
        "was removed, or renamed and edited at once",
        f"pa-eval: {copied} isn't resolved again, and is left as it was (1 problem)",
    ]
    assert files_in(copied) == edited


OUT_OF_FORM = {
    "a payload that quarantines": (
        "payloads/gmail/order.eml",
        ORDER.replace("Message-ID: <order-1@mail.larder.example>\n", ""),
        "payloads/gmail/order.eml: quarantined: email has no Message-ID header",
    ),
    "a wiki that fails lint": (
        "wiki/days/2026-09-21.md",
        "- The jam order ships Friday {ep:gmail/order.eml}\n",
        "wiki/days/2026-09-21.md:1: ",
    ),
    "questions that aren't an eval set": (
        "eval_set.json",
        json.dumps({**QUESTIONS, "time_zone": "Larder/Pantry"}),
        "eval_set.json: time_zone: ",
    ),
    "evidence that names a file that is no payload": (
        "eval_set.json",
        json.dumps(QUESTIONS).replace("gmail/order.eml", "gmail/jam.eml"),
        "eval_set.json: questions.0.evidence: 'gmail/jam.eml' isn't a payload of the reproduction",
    ),
    "an expectation of two lines": (
        "expectation.md",
        "It should say Friday.\nIt says it doesn't know.\n",
        "expectation.md: 2 lines",
    ),
    "a file that isn't a reproduction's": (
        "notes.md",
        "Renamed the larder.\n",
        "notes.md: an unexpected file",
    ),
    "a payload of no source": (
        "payloads/larder/order.eml",
        ORDER,
        "payloads/larder: an unexpected directory",
    ),
}


@pytest.mark.parametrize(("path", "text", "said"), OUT_OF_FORM.values(), ids=OUT_OF_FORM.keys())
def test_an_edit_that_leaves_a_reproduction_out_of_form_is_refused_and_says_where(
    copied: Path, resolve, path: str, text: str, said: str
):
    (copied / path).parent.mkdir(exist_ok=True)
    (copied / path).write_text(text)
    edited = files_in(copied)

    code, out, err = resolve(copied)

    assert (code, out) == (1, "")
    [problem, summary] = err.splitlines()
    assert problem.startswith(said)
    assert summary == f"pa-eval: {copied} isn't resolved again, and is left as it was (1 problem)"
    assert files_in(copied) == edited


def test_evidence_and_citations_written_by_payload_file_are_resolved_too(
    tmp_path: Path, copied: Path, resolve
):
    eval_set = json.loads((copied / "eval_set.json").read_text())
    eval_set["questions"][0]["evidence"] = ["gmail/order.eml", "obsidian/2026-09-21.md"]
    (copied / "eval_set.json").write_text(json.dumps(eval_set))
    day = copied / "wiki" / "days" / "2026-09-21.md"
    day.write_text(day.read_text() + "- [[pru-tanager]] sells it {ep:obsidian/2026-09-21.md}\n")

    code, _, err = resolve(copied)

    assert (code, err) == (0, "")
    ids = ingested(copied / "payloads", tmp_path)
    assert load_eval_set(copied / "eval_set.json").questions[0].evidence == [
        ids["Your marmalade order"],
        ids["## Pantry"],
    ]
    assert day.read_text().endswith(f"sells it [ep:{ids['## Pantry']}]\n")


def test_a_link_in_an_edited_reproduction_is_refused_and_never_written_through(
    tmp_path: Path, copied: Path, resolve
):
    day = copied / "wiki" / "days" / "2026-09-21.md"
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text(day.read_text())
    day.unlink()
    day.symlink_to(elsewhere)
    kept = elsewhere.read_text()

    code, _, err = resolve(copied)

    assert code == 1
    assert err.splitlines()[0] == "wiki/days/2026-09-21.md: a link in place of a file"
    assert day.is_symlink()
    assert elsewhere.read_text() == kept


def test_a_reproduction_whose_wiki_pages_were_all_removed_keeps_no_index(copied: Path, resolve):
    shutil.rmtree(copied / "wiki" / "days")
    shutil.rmtree(copied / "wiki" / "people")

    code, out, err = resolve(copied)

    assert (code, err) == (0, "")
    assert files_under(copied / "wiki") == []
    assert "wiki files: 0\n" in out


NOT_RELEASED = {
    "a reproduction as a session left it": lambda directory: [
        (directory / "reproduction.json").unlink(),
        (directory / "eval_set.json").rename(directory / "questions.json"),
    ],
    "a record that isn't one": lambda directory: (directory / "reproduction.json").write_text(
        '{"finding": "f1"}'
    ),
    "a record for no finding": lambda directory: (directory / "reproduction.json").write_text(
        (directory / "reproduction.json").read_text().replace('"f1"', '"../gooseberry"')
    ),
    "a directory that isn't there": shutil.rmtree,
}


@pytest.mark.parametrize("change", NOT_RELEASED.values(), ids=NOT_RELEASED.keys())
def test_a_directory_that_isnt_a_released_reproduction_is_refused(
    copied: Path, resolve, change: Callable[[Path], object]
):
    change(copied)
    changed = files_in(copied) if copied.exists() else None

    code, out, err = resolve(copied)

    assert (code, out) == (1, "")
    assert err == (
        f"pa-eval: {copied} isn't a released reproduction: it has no reproduction.json that "
        "says which finding it is for and which episode each payload was\n"
    )
    assert (files_in(copied) if copied.exists() else None) == changed


# The shape and the caps. `gooseberry` marks what a test plants itself.

UNEXPECTED = {
    "a file beside the reproduction's own": ("gooseberry.txt", "gooseberry.txt"),
    "a directory of its own": ("gooseberry/notes.md", "gooseberry"),
    "a payload outside a source's directory": (
        "payloads/gooseberry.eml",
        "payloads/gooseberry.eml",
    ),
    "a directory that isn't a source's": ("payloads/gooseberry/order.eml", "payloads/gooseberry"),
    "a payload of another source's format": (
        "payloads/gmail/gooseberry.ics",
        "payloads/gmail/gooseberry.ics",
    ),
    "a directory under a source's": (
        "payloads/gmail/gooseberry/order.eml",
        "payloads/gmail/gooseberry",
    ),
    "a hidden payload": ("payloads/gmail/.gooseberry.eml", "payloads/gmail/.gooseberry.eml"),
    "a payload named with a space": (
        "payloads/gmail/goose berry.eml",
        "payloads/gmail/goose berry.eml",
    ),
    "a wiki file that isn't Markdown": (
        "wiki/people/gooseberry.txt",
        "wiki/people/gooseberry.txt",
    ),
    "a wiki folder that isn't one": ("wiki/gooseberry/page.md", "wiki/gooseberry"),
    "a directory in a wiki folder": ("wiki/people/gooseberry/page.md", "wiki/people/gooseberry"),
    "a wiki's index, which a release writes": ("wiki/index.md", "wiki/index.md"),
    "instructions in the wiki": ("wiki/CLAUDE.md", "wiki/CLAUDE.md"),
}
"""A file a reproduction may not hold, and the path that is refused for it."""


@pytest.mark.parametrize(("path", "refused"), UNEXPECTED.values(), ids=UNEXPECTED.keys())
def test_a_reproduction_with_an_unexpected_file_is_refused(
    desk, leave, reproduction, path: str, refused: str
):
    reproduction[path] = "The marmalade order ships on Friday.\n"
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), shape=1)

    assert f"- output/reproductions/f1/{refused}: an unexpected " in notes


def test_a_directory_that_isnt_named_for_a_finding_is_refused(desk, leave, reproduction):
    left = leave(reproduction)
    (left / "reproductions" / "gooseberry").mkdir()
    (left / "reproductions" / "gooseberry.md").write_text("The marmalade order.\n")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), shape=2)

    assert "- output/reproductions/gooseberry: an unexpected directory" in notes
    assert "- output/reproductions/gooseberry.md: an unexpected file" in notes


LINKED = [
    "reproductions",
    "reproductions/f1",
    "reproductions/f1/expectation.md",
    "reproductions/f1/questions.json",
    "reproductions/f1/payloads",
    "reproductions/f1/payloads/gmail",
    "reproductions/f1/payloads/gmail/order.eml",
    "reproductions/f1/wiki",
    "reproductions/f1/wiki/people",
    "reproductions/f1/wiki/people/pru-tanager.md",
]


@pytest.mark.parametrize("path", LINKED)
def test_a_link_in_a_reproduction_is_refused_and_never_followed(
    tmp_path: Path, desk, leave, reproduction, path: str
):
    # The link leads to what would pass, were it read.
    left = leave(reproduction)
    elsewhere = tmp_path / "elsewhere"
    (left / path).rename(elsewhere)
    (left / path).symlink_to(elsewhere, target_is_directory=elsewhere.is_dir())
    desk.diagnose(left)
    assert (desk.private / "output" / path).is_symlink()

    notes = desk.refused(desk.release(), shape=1)

    assert f"- output/{path}: a link in place of a file" in notes


def test_a_file_that_isnt_text_is_refused(desk, leave, reproduction):
    left = leave(reproduction)
    (left / "reproductions/f1/payloads/gmail/order.eml").write_bytes(b"\xff\xfe\x00marmalade")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/reproductions/f1/payloads/gmail/order.eml: not UTF-8 text" in notes


@pytest.mark.parametrize("name", ["expectation.md", "questions.json", "payloads"])
def test_a_reproduction_without_one_of_its_parts_is_refused(desk, leave, reproduction, name: str):
    kept = {path: text for path, text in reproduction.items() if not path.startswith(name)}
    assert len(kept) < len(reproduction)
    desk.diagnose(leave(kept))

    notes = desk.refused(desk.release(), shape=1)

    assert f"- output/reproductions/f1/{name}: missing" in notes


def mail(number: int) -> str:
    """One more mail about the marmalade, with an id of its own."""
    return ORDER.replace("order-1@", f"order-more-{number}@").replace(
        "two boxes", f"{number} boxes"
    )


def day(number: int) -> str:
    """A day log's path, for one more day in October."""
    return f"wiki/days/2026-10-{number:02}.md"


def test_a_reproduction_over_the_cap_on_payloads_is_refused(desk, leave, reproduction):
    more = {f"payloads/gmail/more-{number}.eml": mail(number) for number in range(38)}
    desk.diagnose(leave(reproduction | more))

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/reproductions/f1/payloads: 41 payloads, over the cap of 40" in notes


def test_a_reproduction_over_the_cap_on_wiki_files_is_refused(desk, leave, reproduction):
    more = {day(number): DAY for number in range(1, 20)}
    desk.diagnose(leave(reproduction | more))

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/reproductions/f1/wiki: 21 files, over the cap of 20" in notes


def test_a_reproduction_over_the_cap_on_bytes_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/order.eml"] = ORDER + "More marmalade than was ordered.\n" * 7000
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/reproductions/f1: " in notes
    assert " bytes, over the cap of 200000" in notes


def test_a_reproduction_at_every_cap_is_released(desk, leave, reproduction):
    more = {f"payloads/gmail/more-{number}.eml": mail(number) for number in range(37)}
    more |= {day(number): DAY for number in range(1, 19)}
    files = reproduction | more
    padding = 200_000 - sum(len(text.encode()) for text in files.values())
    files["payloads/gmail/order.eml"] += "x" * padding
    desk.diagnose(leave(files))

    ran = desk.release()

    assert ran.code == 0
    assert "payloads: 40\n" in ran.out
    record = json.loads((desk.released / "reproductions/f1/reproduction.json").read_text())
    assert (record["payloads"], record["wiki_files"]) == (40, 20)


# What a reproduction has to say, and how.


def asked(**changes: Any) -> str:
    """The clean reproduction's questions file, with `changes` to its first question."""
    first, *rest = QUESTIONS["questions"]
    return json.dumps({**QUESTIONS, "questions": [{**first, **changes}, *rest]})


def test_a_finding_whose_reproduction_isnt_there_is_refused(desk, leave):
    desk.diagnose(leave({}))

    notes = desk.refused(desk.release(), reproductions=1)

    assert "- findings.json: f1.reproduction: there is no reproductions/f1" in notes


def test_a_reproduction_that_no_finding_names_is_refused(desk, leave, reproduction):
    left = leave(reproduction)
    (left / "reproductions" / "f1").rename(left / "reproductions" / "f2")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), reproductions=2)

    assert "- findings.json: f1.reproduction: there is no reproductions/f1" in notes
    assert "- reproductions/f2: no finding names it as its reproduction" in notes


NOT_AN_EVAL_SET = {
    "prose": "When does the marmalade ship? Friday.\n",
    "no owner": json.dumps({key: value for key, value in QUESTIONS.items() if key != "owner"}),
    "a category off the list": asked(category="gooseberry"),
    "a field an eval set doesn't hold": asked(gooseberry="jam"),
    "evidence that isn't a list": asked(evidence="gmail/order.eml"),
    "a time zone that isn't one": json.dumps({**QUESTIONS, "time_zone": "Gooseberry/Jam"}),
    "two questions with one id": asked(id="marmalade-pickup"),
}


@pytest.mark.parametrize("questions", NOT_AN_EVAL_SET.values(), ids=NOT_AN_EVAL_SET.keys())
def test_questions_that_arent_an_eval_set_are_refused(desk, leave, reproduction, questions: str):
    reproduction["questions.json"] = questions
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), reproductions=1)

    assert "- reproductions/f1/questions.json: " in notes


@pytest.mark.parametrize(
    "evidence",
    [
        "gmail/gooseberry.eml",
        "order.eml",
        "payloads/gmail/order.eml",
        "../f2/payloads/gmail/order.eml",
        # An episode's id, which only a release works out.
        "gmail_94f6b4cbc55a3b85",
    ],
)
def test_evidence_that_names_no_payload_of_the_reproduction_is_refused(
    desk, leave, reproduction, evidence: str
):
    reproduction["questions.json"] = asked(evidence=["gmail/order.eml", evidence])
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), reproductions=1)

    assert (
        f"- reproductions/f1/questions.json: questions.0.evidence: {evidence!r} isn't a payload "
        "of the reproduction"
    ) in notes


def test_an_adversarial_episode_that_names_no_payload_of_the_reproduction_is_refused(
    desk, leave, reproduction
):
    reproduction["questions.json"] = json.dumps({**QUESTIONS, "adversarial": ["gmail/goose.eml"]})
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), reproductions=1)

    assert "- reproductions/f1/questions.json: adversarial: 'gmail/goose.eml' isn't a" in notes


def test_adversarial_payloads_are_resolved_as_evidence_is(desk, leave, reproduction):
    reproduction["questions.json"] = json.dumps({**QUESTIONS, "adversarial": ["gmail/order.eml"]})
    desk.diagnose(leave(reproduction))

    assert desk.release().code == 0

    eval_set = json.loads((desk.released / "reproductions/f1/eval_set.json").read_text())
    assert eval_set["adversarial"] == eval_set["questions"][0]["evidence"]


def test_a_wiki_that_cites_no_payload_of_the_reproduction_is_refused(desk, leave, reproduction):
    reproduction["wiki/days/2026-09-21.md"] = DAY.replace("gmail/order.eml", "gmail/goose.eml")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), reproductions=1)

    assert (
        "- reproductions/f1/wiki/days/2026-09-21.md:1: cites 'gmail/goose.eml', which isn't a "
        "payload of the reproduction"
    ) in notes


NOT_ONE_LINE = {
    "nothing": "",
    "a blank line": "\n",
    "two lines": "The assistant should say Friday.\nIt says it doesn't know.\n",
    "a line over 400 characters": "It should say Friday. " * 19 + "\n",
}


@pytest.mark.parametrize("expectation", NOT_ONE_LINE.values(), ids=NOT_ONE_LINE.keys())
def test_an_expectation_that_isnt_one_line_is_refused(desk, leave, reproduction, expectation: str):
    reproduction["expectation.md"] = expectation
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), reproductions=1)

    assert "- reproductions/f1/expectation.md: " in notes


# The owner is the synthetic corpus's fictional one.

OWNER_ENV = Path(__file__).resolve().parents[1] / "corpus" / "owner.env"

NOT_THE_FICTIONAL_OWNER = {
    "another name": {**OWNER, "name": "Gooseberry Jam"},
    "another address": {**OWNER, "email_addresses": ["gooseberry@example.com"]},
    "an address more": {**OWNER, "email_addresses": ["argus@example.com", "g@example.org"]},
    "another name they go by": {**OWNER, "other_names": ["Goose"]},
    "no other name": {**OWNER, "other_names": []},
}


@pytest.mark.parametrize(
    "owner", NOT_THE_FICTIONAL_OWNER.values(), ids=NOT_THE_FICTIONAL_OWNER.keys()
)
def test_a_reproduction_whose_owner_isnt_the_fictional_one_is_refused(
    desk, leave, reproduction, owner: dict[str, Any]
):
    reproduction["questions.json"] = json.dumps({**QUESTIONS, "owner": owner})
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), owner=1)

    assert (
        "- reproductions/f1/questions.json: owner: isn't the synthetic corpus's fictional owner"
    ) in notes


def test_the_fictional_owner_is_the_one_the_synthetic_corpus_is_written_for():
    # The clean reproduction's owner, which a release takes, is the corpus's own.
    said = dict(line.split("=", 1) for line in OWNER_ENV.read_text().splitlines())

    assert said["PA_OWNER_NAME"].strip("'") == OWNER["name"]
    assert said["PA_OWNER_EMAILS"].split(",") == OWNER["email_addresses"]
    assert said["PA_OWNER_OTHER_NAMES"].split(",") == OWNER["other_names"]


# It runs: the payloads ingest, and the wiki lints.

QUARANTINES = {
    "a mail without an id": (
        "payloads/gmail/order.eml",
        ORDER.replace("Message-ID: <order-1@mail.larder.example>\n", ""),
        "email has no Message-ID header",
    ),
    "a note without its capture header": (
        "payloads/obsidian/2026-09-21.md",
        "## Pantry\n\nOrdered marmalade.\n",
        "",
    ),
    "a calendar file that holds no event": (
        "payloads/icloud_calendar/pickup.ics",
        "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
        "",
    ),
    "a chat that isn't an exchange": (
        "payloads/assistant_chat/chat.json",
        json.dumps({"session_id": "chat-1", "turns": []}),
        "",
    ),
    "a second mail with the first one's id": (
        "payloads/gmail/second-order.eml",
        ORDER.replace("two boxes", "three boxes"),
        "already exists in L0 with different content",
    ),
}


@pytest.mark.parametrize(
    ("path", "payload", "reason"), QUARANTINES.values(), ids=QUARANTINES.keys()
)
def test_a_reproduction_with_a_payload_that_quarantines_is_refused(
    desk, leave, reproduction, path: str, payload: str, reason: str
):
    reproduction[path] = payload
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), ingest=1)

    assert f"- reproductions/f1/{path}: quarantined: " in notes
    assert reason in notes


def test_the_same_payload_twice_is_one_episode_and_is_released(desk, leave, reproduction):
    reproduction["payloads/gmail/order-again.eml"] = ORDER
    desk.diagnose(leave(reproduction))

    ran = desk.release()

    assert ran.code == 0
    assert "payloads: 4\n" in ran.out


FAILS_LINT = {
    "a claim without a citation": (
        "wiki/people/pru-tanager.md",
        PERSON.replace(" {ep:gmail/order.eml}", ""),
    ),
    "an episode that isn't one of the reproduction's": (
        "wiki/days/2026-09-21.md",
        DAY.replace("{ep:gmail/order.eml}", "[ep:gmail_abcdefabcdefabcd]"),
    ),
    "an address no payload holds": (
        "wiki/people/pru-tanager.md",
        PERSON.replace("pru@larder.example", "gooseberry@larder.example"),
    ),
    "a day log line that links nothing": (
        "wiki/days/2026-09-21.md",
        "- The marmalade order ships Friday {ep:gmail/order.eml}\n",
    ),
    "a retired slug that isn't written as one": ("wiki/retired.md", "Pru left the larder.\n"),
}


@pytest.mark.parametrize(("path", "text"), FAILS_LINT.values(), ids=FAILS_LINT.keys())
def test_a_reproduction_whose_wiki_fails_lint_is_refused(
    desk, leave, reproduction, path: str, text: str
):
    reproduction[path] = text
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), lint=1)

    assert f"- reproductions/f1/{path}" in notes


def test_a_reproduction_without_a_wiki_is_released_without_one(desk, leave, reproduction):
    kept = {path: text for path, text in reproduction.items() if not path.startswith("wiki/")}
    desk.diagnose(leave(kept))

    assert desk.release().code == 0

    assert not (desk.released / "reproductions" / "f1" / "wiki").exists()
    record = json.loads((desk.released / "reproductions/f1/reproduction.json").read_text())
    assert record["wiki_files"] == 0


# Nothing real: every rule the findings are held to, over every file of a reproduction.


def in_a_mail(files: dict[str, str], text: str) -> str:
    files["payloads/gmail/order.eml"] += f"{text}\n"
    return "payloads/gmail/order.eml:9"


def in_a_note(files: dict[str, str], text: str) -> str:
    files["payloads/obsidian/2026-09-21.md"] += f"{text}\n"
    return "payloads/obsidian/2026-09-21.md:7"


def in_a_page(files: dict[str, str], text: str) -> str:
    files["wiki/people/pru-tanager.md"] += f"{text} {{ep:gmail/order.eml}}\n"
    return "wiki/people/pru-tanager.md:17"


def in_a_question(files: dict[str, str], text: str) -> str:
    files["questions.json"] = asked(question=f"What about this: {text}?")
    return "questions.json: questions.0.question"


def in_an_expected_answer(files: dict[str, str], text: str) -> str:
    files["questions.json"] = asked(expected_answer=f"Friday. {text}")
    return "questions.json: questions.0.expected_answer"


def in_the_expectation(files: dict[str, str], text: str) -> str:
    files["expectation.md"] = f"The assistant should mention this, and doesn't: {text}\n"
    return "expectation.md"


PLACES = [in_a_mail, in_a_note, in_a_page, in_a_question, in_an_expected_answer, in_the_expectation]
"""Each puts a text somewhere in a reproduction, and returns where the notes say it is."""

in_every_place = pytest.mark.parametrize("place", PLACES, ids=lambda place: place.__name__)


@in_every_place
def test_a_reproduction_that_holds_a_name_of_two_words_from_the_case_is_refused(
    desk, leave, reproduction, place
):
    where = place(reproduction, "ottoline VARGA")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert (
        f"- reproductions/f1/{where}: the name 'ottoline varga', which the case holds (a "
        "participant's display name)\n"
    ) in notes


def test_a_payload_named_for_someone_in_the_case_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/Wilhelmina_Stroud.eml"] = mail(2)
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert (
        "- reproductions/f1/payloads/gmail/Wilhelmina_Stroud.eml: its name: the name "
        "'wilhelmina stroud'"
    ) in notes


def test_a_wiki_that_links_someone_in_the_case_is_refused(desk, leave, reproduction):
    reproduction["wiki/days/2026-09-21.md"] += (
        "- [[wilhelmina-stroud]] asked about the marmalade {ep:gmail/order.eml}\n"
    )
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert "- reproductions/f1/wiki/days/2026-09-21.md:2: the name 'wilhelmina stroud'" in notes


ONE_WORD_NAMES = {
    "a participant's display name": "Billie",
    "a person's alias": "Mina",
    "an organization's page name": "Quince",
    "a slug that is linked and has no page, with a capital": "Fennimore",
}


@in_every_place
def test_a_reproduction_that_holds_a_name_of_one_word_from_the_case_is_refused(
    desk, leave, reproduction, place
):
    where = place(reproduction, "Billie's marmalade")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), **{"one-word-names": 1})

    assert (
        f"- reproductions/f1/{where}: the name 'Billie', which the case holds (a participant's "
        "display name)\n"
    ) in notes


@pytest.mark.parametrize("name", ONE_WORD_NAMES.values(), ids=ONE_WORD_NAMES.keys())
def test_a_name_of_one_word_is_refused_in_the_letter_case_the_case_holds_it_in(
    desk, leave, reproduction, name: str
):
    in_a_mail(reproduction, f"Ask {name} about the jars.")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), **{"one-word-names": 1})

    assert f"- reproductions/f1/payloads/gmail/order.eml:9: the name {name!r}" in notes


def test_a_payload_named_with_a_one_word_name_from_the_case_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/Quince-order.eml"] = mail(2)
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), **{"one-word-names": 1})

    assert "- reproductions/f1/payloads/gmail/Quince-order.eml: its name: the name 'Qui" in notes


NOT_A_HELD_NAME = {
    "a one-word name in lower case": "billie and mina went to the quince stall",
    "a one-word name in capitals": "BILLIE, MINA, QUINCE",
    "a one-word name inside a longer word": "Billies and Minas and Quinces",
    "one word of a longer name": "Ottoline wrote, and so did someone called Stroud",
    "a topic's slug": "Heron and heron",
    "one of the fictional owner's names, which the case holds too": "Gus McNevans, or Argus",
}


@pytest.mark.parametrize("text", NOT_A_HELD_NAME.values(), ids=NOT_A_HELD_NAME.keys())
def test_a_name_the_case_doesnt_hold_that_way_passes(desk, leave, reproduction, text: str):
    in_a_mail(reproduction, text)
    in_the_expectation(reproduction, text)
    desk.diagnose(leave(reproduction))

    assert desk.release().code == 0

    released = desk.released / "reproductions" / "f1"
    assert text in (released / "payloads" / "gmail" / "order.eml").read_text()


def test_a_one_word_name_passes_in_the_findings(desk, leavings, well_formed):
    # Findings are prose about code, where a word like a name is mostly a word.
    well_formed["summary"] = "Billie and Quince share a page, which Mina never links."
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0


# A domain that must be refused is put together here from its parts, since a dotted name that
# is in the repository passes, and this file is in it.

STUDIO = "sgraffito-works" + "." + "com"

OUTSIDE_THE_RESERVED_DOMAINS = {
    "an address": "ovarga" + "@" + STUDIO,
    "a domain": STUDIO,
    "a link": f"https://{STUDIO}/classes",
    "a file's name that isn't in the repository": "quince-ledger" + "." + "md",
}


@in_every_place
@pytest.mark.parametrize(
    "text", OUTSIDE_THE_RESERVED_DOMAINS.values(), ids=OUTSIDE_THE_RESERVED_DOMAINS.keys()
)
def test_a_reproduction_with_an_address_or_a_domain_outside_the_reserved_ones_is_refused(
    desk, leave, reproduction, place, text: str
):
    where = place(reproduction, f"See {text} for it")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), addresses=1)

    assert f"- reproductions/f1/{where}: the " in notes
    assert "is outside the reserved example domains" in notes


# What the repository holds excuses nothing in a reproduction: it becomes a fixture, where
# every address, link, and domain is a choice. This file is in the repository, so each of these
# is a dotted name the repository holds.

HELD_BY_THE_REPOSITORY = {
    "a link to a host the repository names": "https://github.com/-no-such-account-/records",
    "a link without its scheme": "claude.ai/chat/no-such-chat",
    "a host the repository names": "gmail.com",
    "a module of the repository": "pa_core.librarian.lint",
    "a file of the repository": "dreaming.py",
    "a link whose host is named as one of its own files is": "https://order.eml/receipts",
}


@pytest.mark.parametrize("place", [in_a_mail, in_a_page, in_a_question, in_the_expectation])
@pytest.mark.parametrize("text", HELD_BY_THE_REPOSITORY.values(), ids=HELD_BY_THE_REPOSITORY.keys())
def test_a_dotted_name_is_refused_in_a_reproduction_though_the_repository_holds_it(
    desk, leave, reproduction, place, text: str
):
    where = place(reproduction, f"It is all at {text} now")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), addresses=1)

    assert f"- reproductions/f1/{where}: the " in notes
    assert "is outside the reserved example domains" in notes


def test_a_reproduction_may_name_its_own_files(desk, leave, reproduction):
    # A note is captured from a file of the vault, which its payload names.
    reproduction["payloads/obsidian/pantry.md"] = NOTE.replace("2026-09-21", "2026-09-20")
    in_a_mail(reproduction, "The receipt is in order.eml, and pickup.ics has the time.")
    in_the_expectation(reproduction, "order.eml and pru-tanager.md say so, as 2026-09-21.md does")
    desk.diagnose(leave(reproduction))

    ran = desk.release()

    assert ran.code == 0
    assert "payloads: 4\n" in ran.out


def test_a_mail_from_an_address_outside_the_reserved_domains_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/order.eml"] = ORDER.replace("@larder.example", "@" + STUDIO)
    # The page would list an address no payload holds, which is lint's to refuse.
    del reproduction["wiki/people/pru-tanager.md"]
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), addresses=1)

    assert "- reproductions/f1/payloads/gmail/order.eml:3: the address " in notes


@in_every_place
def test_a_reproduction_that_repeats_six_words_from_the_case_is_refused(
    desk, leave, reproduction, place
):
    where = place(reproduction, BODY_LINE.upper())
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), quotes=1)

    assert f"- reproductions/f1/{where}: 8 words in a row that the case holds (" in notes
    assert f"'s body): {BODY_LINE.lower()!r}\n" in notes


def test_words_from_a_wiki_page_of_the_case_are_refused_and_fewer_than_six_pass(
    desk, leave, reproduction
):
    five = " ".join(BODY_LINE.split()[:5])
    in_a_mail(reproduction, five)
    in_a_page(reproduction, PAGE_LINE)
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), quotes=1)

    # The quote runs on into the citation that follows it, in the page and in the case.
    assert (
        "- reproductions/f1/wiki/people/pru-tanager.md:17: 12 words in a row that the case "
        f"holds (wiki/people/wilhelmina-stroud.md): '{PAGE_LINE.lower()} ep gmail'\n"
    ) in notes


def test_words_the_repository_and_the_referral_hold_pass_in_a_reproduction(
    desk, referral, leave, reproduction
):
    referral.write_text(f"{referral.read_text()}\nA run answered: {BODY_LINE}.\n")
    in_a_mail(reproduction, BODY_LINE)
    # The fixture corpus is in the case, and in the repository.
    in_a_note(
        reproduction,
        "Mara thinks weekday evenings work best, since her late shifts are mostly weekends.",
    )
    desk.diagnose(leave(reproduction))

    assert desk.release().code == 0


LONG_NUMBERS = {
    "seven digits in a row": "5550142",
    "a number in groups": "(555) 014-2368",
    "a time written as one number, with no date in it": "20261340T990000",
}


@in_every_place
@pytest.mark.parametrize("text", LONG_NUMBERS.values(), ids=LONG_NUMBERS.keys())
def test_a_reproduction_with_a_long_number_is_refused(desk, leave, reproduction, place, text: str):
    where = place(reproduction, f"Quote {text} when you call")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), digits=1)

    assert f"- reproductions/f1/{where}: a number of " in notes


def test_a_payload_named_with_a_long_number_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/order-5550142.eml"] = mail(2)
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), digits=1)

    assert "- reproductions/f1/payloads/gmail/order-5550142.eml: its name: a number of 7" in notes


CHAT = {
    "session_id": "chat-1",
    "message_id": "message-1",
    "started_at": "2026-09-21T15:12:04.512345Z",
    "ended_at": "2026-09-21T15:12:31.907120+00:00",
    "turns": [
        {"role": "owner", "text": "When does my marmalade ship?"},
        {"role": "assistant", "text": "On Friday, the 25th, at 10:05."},
    ],
}

DATES_AND_TIMES = {
    "a calendar's date": ("payloads/icloud_calendar/pickup.ics", "DTSTART;VALUE=DATE:20260926"),
    "a calendar's time": ("payloads/icloud_calendar/pickup.ics", "20260926T100000"),
    "a calendar's time in UTC": ("payloads/icloud_calendar/pickup.ics", "20260921T151000Z"),
    "a mail's date": ("payloads/gmail/order.eml", "Date: Mon, 21 Sep 2026 10:05:00 -0500"),
    "a note's capture time": ("payloads/obsidian/2026-09-21.md", "2026-09-21T21:00:00-05:00"),
    "a time to the microsecond": ("payloads/assistant_chat/chat.json", "15:12:04.512345Z"),
}


@pytest.mark.parametrize(("path", "written"), DATES_AND_TIMES.values(), ids=DATES_AND_TIMES.keys())
def test_a_date_or_a_time_in_a_payload_isnt_a_long_number(
    desk, leave, reproduction, path: str, written: str
):
    if "VALUE=DATE" in written:
        all_day = PICKUP.replace(";TZID=America/Chicago:20260926T100000", ";VALUE=DATE:20260926")
        all_day = all_day.replace(";TZID=America/Chicago:20260926T103000", ";VALUE=DATE:20260927")
        reproduction["payloads/icloud_calendar/pickup.ics"] = all_day
    reproduction["payloads/assistant_chat/chat.json"] = json.dumps(CHAT, indent=2)
    assert written in reproduction[path]
    desk.diagnose(leave(reproduction))

    ran = desk.release()

    assert ran.code == 0
    assert "payloads: 4\n" in ran.out


@pytest.mark.parametrize("place", [in_a_page, in_a_question, in_the_expectation])
def test_a_time_written_as_one_number_is_a_long_number_outside_a_payload(
    desk, leave, reproduction, place
):
    place(reproduction, "It was sent at 20260926T100000")
    desk.diagnose(leave(reproduction))

    desk.refused(desk.release(), digits=1)


def test_an_episode_of_the_case_named_in_a_reproduction_is_refused(desk, cut, leave, reproduction):
    # An episode's id says nothing, and it still stays out of what becomes a fixture.
    episode = "gmail_94f6b4cbc55a3b85"
    assert episode in cut.episode_ids()
    in_a_mail(reproduction, f"As I said in [ep:{episode}].")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), episodes=1)

    assert (
        f"- reproductions/f1/payloads/gmail/order.eml:9: {episode!r} is an episode in the case"
    ) in notes


# What ingest reads out of a payload is checked too, since a payload can write it another way.

ENCODED = """\
Message-ID: <order-2@mail.larder.example>
Date: Mon, 21 Sep 2026 11:05:00 -0500
From: Pru Tanager <pru@larder.example>
To: Argus McNevans <argus@example.com>
Subject: Your second marmalade order
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: base64

"""


def encoded(text: str) -> str:
    return ENCODED + base64.b64encode(text.encode()).decode() + "\n"


def test_a_name_a_payload_holds_encoded_is_refused(desk, leave, reproduction):
    reproduction["payloads/gmail/second.eml"] = encoded("Ottoline Varga packed it.\n")
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert (
        "- reproductions/f1/payloads/gmail/second.eml: its body (text/plain), decoded: the "
        "name 'ottoline varga'"
    ) in notes


def test_a_name_split_over_a_folded_line_of_a_payload_is_refused(desk, leave, reproduction):
    reproduction["payloads/icloud_calendar/pickup.ics"] = PICKUP.replace(
        "STATUS:CONFIRMED",
        "ATTENDEE;CN=Otto\n line Varga:mailto:ovarga@larder.example\nSTATUS:CONFIRMED",
    )
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert (
        "- reproductions/f1/payloads/icloud_calendar/pickup.ics: its lines, unfolded: the "
        "name 'ottoline varga'"
    ) in notes


def test_every_word_of_an_encoded_payload_is_held_to_every_rule(desk, leave, reproduction):
    reproduction["payloads/gmail/second.eml"] = encoded(
        f"{BODY_LINE}. Billie can be reached at {'ovarga' + '@' + STUDIO} or 555-014-2368.\n"
    )
    desk.diagnose(leave(reproduction))

    desk.refused(desk.release(), addresses=1, quotes=1, digits=1, **{"one-word-names": 1})


# Every decoding of a payload is checked, not only the one ingest reads: a payload is released
# as it was left, byte for byte, with every part and header a mail client would show.

ALTERNATIVES = """\
Message-ID: <order-3@mail.larder.example>
Date: Mon, 21 Sep 2026 12:05:00 -0500
From: Pru Tanager <pru@larder.example>
To: Argus McNevans <argus@example.com>
Subject: Your third marmalade order
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="jar"

--jar
Content-Type: text/plain; charset="utf-8"

The third marmalade order ships on Monday.
--jar
Content-Type: text/html; charset="utf-8"
Content-Transfer-Encoding: {encoding}

{html}
--jar--
"""


def with_an_html_part(html: str, encoding: str = "base64") -> str:
    """A mail whose body ingest reads from its plain part, and never from its HTML one."""
    if encoding == "base64":
        html = base64.b64encode(html.encode()).decode()
    return ALTERNATIVES.format(encoding=encoding, html=html)


def encoded_word(text: str) -> str:
    return f"=?utf-8?b?{base64.b64encode(text.encode()).decode()}?="


IN_PIECES = 'Content-Disposition: inline; filename*0="Otto"; filename*1="line Varga"\n'
"""A parameter written in two pieces (RFC 2231), which no line holds whole."""

UNREAD_BY_INGEST = {
    "an HTML part in base64": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>Ottoline Varga packed it.</p>"),
        "part 2 (text/html), decoded",
    ),
    "an HTML part whose soft line break splits it": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>Otto=\nline=20Varga packed it.</p>", "quoted-printable"),
        "part 2 (text/html), decoded",
    ),
    "an HTML part whose tags split it": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>Otto<b></b>line Varga packed it.</p>"),
        "part 2 (text/html), as it shows",
    ),
    "an HTML part that writes it in character references": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>&#79;ttoline&nbsp;Varga packed it.</p>"),
        "part 2 (text/html), decoded, with its character references read",
    ),
    "a header ingest doesn't read, in an encoded word": (
        "payloads/gmail/order.eml",
        ORDER.replace("Subject:", f"X-Packed-By: {encoded_word('Ottoline Varga')}\nSubject:"),
        "its X-Packed-By header, decoded",
    ),
    "a header's parameter, in two pieces": (
        "payloads/gmail/order.eml",
        ORDER.replace("Subject:", f"{IN_PIECES}Subject:"),
        "its Content-Disposition header's filename, decoded",
    ),
    "a calendar property ingest doesn't read, folded": (
        "payloads/icloud_calendar/pickup.ics",
        PICKUP.replace("STATUS:", "COMMENT:Packed by Otto\n line Varga\nSTATUS:"),
        "its lines, unfolded",
    ),
    "a calendar property ingest doesn't read, with an escaped line break": (
        "payloads/icloud_calendar/pickup.ics",
        PICKUP.replace("STATUS:", "COMMENT:Packed by Ottoline\\nVarga\nSTATUS:"),
        "its text, unescaped",
    ),
    "a chat's session, in an escape": (
        "payloads/assistant_chat/chat.json",
        json.dumps(CHAT, indent=2).replace("chat-1", "\\u004fttoline-Varga"),
        "with its backslash escapes read",
    ),
    "a note, in a character reference": (
        "payloads/obsidian/2026-09-21.md",
        NOTE + "Packed by Ottoline&#32;Varga.\n",
        "with its character references read",
    ),
    "a note's link, percent-encoded": (
        "payloads/obsidian/2026-09-21.md",
        NOTE + "[The packer](https://larder.example/staff/Ottoline%20Varga)\n",
        "with its percent-encoding read",
    ),
}


@pytest.mark.parametrize(
    ("path", "payload", "decoding"), UNREAD_BY_INGEST.values(), ids=UNREAD_BY_INGEST.keys()
)
def test_a_name_a_payload_holds_where_ingest_doesnt_read_it_is_refused(
    desk, leave, reproduction, path: str, payload: str, decoding: str
):
    reproduction[path] = payload
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert f"- reproductions/f1/{path}: {decoding}: the name 'ottoline varga'" in notes


def test_every_word_of_a_part_ingest_doesnt_read_is_held_to_every_rule(desk, leave, reproduction):
    reproduction["payloads/gmail/third.eml"] = with_an_html_part(
        f"<p>{BODY_LINE}. Billie can be reached at {'ovarga' + '@' + STUDIO} or 555-014-2368.</p>"
    )
    desk.diagnose(leave(reproduction))

    desk.refused(desk.release(), addresses=1, quotes=1, digits=1, **{"one-word-names": 1})


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable", "8bit"])
def test_a_mail_with_an_html_part_that_holds_nothing_real_is_released_as_it_was_left(
    desk, leave, reproduction, encoding: str
):
    # A fault in how an HTML mail is ingested can still be reproduced.
    mail = with_an_html_part(
        "<p>The third order ships on <b>Monday</b> &amp; no later.</p>", encoding
    )
    reproduction["payloads/gmail/third.eml"] = mail
    desk.diagnose(leave(reproduction))

    assert desk.release().code == 0

    released = desk.released / "reproductions" / "f1" / "payloads" / "gmail" / "third.eml"
    assert released.read_text() == mail


ATTACHED = ALTERNATIVES.replace("multipart/alternative", "multipart/mixed")

CANT_BE_DECODED = {
    "an attachment that isn't text": (
        "payloads/gmail/third.eml",
        ATTACHED.replace('text/html; charset="utf-8"', "application/octet-stream").format(
            encoding="base64", html=base64.b64encode(b"Ottoline Varga").decode()
        ),
        "part 2 (application/octet-stream) isn't text",
    ),
    "a part in an encoding nobody reads": (
        "payloads/gmail/third.eml",
        with_an_html_part("begin 644 jar\n$:F%R\n`\nend", "x-uuencode"),
        "part 2 (text/html) is in an encoding that isn't read (x-uuencode)",
    ),
    "a part in a character set nobody knows": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>Jars.</p>").replace(
            'text/html; charset="utf-8"', "text/html; charset=x-jar"
        ),
        "part 2 (text/html) isn't text in the character set it names",
    ),
    "a part that says it is base64 and isn't": (
        "payloads/gmail/third.eml",
        ALTERNATIVES.format(encoding="base64", html="<p>Jars, not base64.</p>"),
        "part 2 (text/html) is encoded as base64, and isn't base64",
    ),
    "a part that says twice how it is encoded": (
        "payloads/gmail/third.eml",
        with_an_html_part("<p>Jars.</p>").replace(
            "Content-Transfer-Encoding: base64",
            "Content-Transfer-Encoding: 7bit\n" * 2 + "X-Jar: 1",
        ),
        "part 2 says twice what it is or how it is encoded",
    ),
    "a kind of text that has encodings of its own": (
        "payloads/gmail/third.eml",
        with_an_html_part("{\\rtf1 Jars.}", "7bit").replace("text/html", "text/rtf"),
        "part 2 (text/rtf) is a kind of text that isn't read",
    ),
    "a header's encoded word in a character set nobody knows": (
        "payloads/gmail/order.eml",
        ORDER.replace("Subject:", "X-Packed-By: =?x-jar?q?Jars?=\nSubject:"),
        "its X-Packed-By header holds an encoded word that can't be read",
    ),
    "a calendar property in an encoding of its own": (
        "payloads/icloud_calendar/pickup.ics",
        PICKUP.replace("STATUS:", "ATTACH;ENCODING=BASE64;VALUE=BINARY:SmFycw==\nSTATUS:"),
        "the property on line 12, unfolded, is in an encoding of its own",
    ),
    "data in a link of a note": (
        "payloads/obsidian/2026-09-21.md",
        NOTE + "![The jar](data:image/png;base64,SmFycw==)\n",
        "1 `data:` URIs that carry their data in base64",
    ),
    "data in a link of a wiki page": (
        "wiki/people/pru-tanager.md",
        PERSON + "![The jar](data:image/png;base64,SmFycw==) {ep:gmail/order.eml}\n",
        "1 `data:` URIs that carry their data in base64",
    ),
}


@pytest.mark.parametrize(
    ("path", "text", "why"), CANT_BE_DECODED.values(), ids=CANT_BE_DECODED.keys()
)
def test_a_reproduction_that_holds_what_cant_be_decoded_to_text_is_refused(
    desk, leave, reproduction, path: str, text: str, why: str
):
    # What can't be read can't be checked, and a payload is released whole.
    reproduction[path] = text
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), encodings=1)

    assert f"- reproductions/f1/{path}" in notes
    assert why in notes


def test_the_index_a_release_writes_is_checked_as_the_pages_it_is_made_from_are(
    desk, leave, reproduction
):
    # A page's name and its summary are far apart in the page, and side by side in the index.
    reproduction["wiki/topics/ottoline.md"] = (
        "---\ntype: topic\naliases: []\nstatus: active\n"
        "summary: Varga jars, which the larder stocks.\nupdated: 2026-09-21\n---\n# Ottoline\n\n"
        "## Current state\nThe larder stocks them. {ep:gmail/order.eml}\n\n"
        "## Decisions\n\n## Open loops\n\n## Notes\n"
    )
    desk.diagnose(leave(reproduction))

    notes = desk.refused(desk.release(), names=1)

    assert "- reproductions/f1/wiki/index.md:" in notes


# A refusal, whatever it found.


def test_a_refused_reproduction_releases_neither_it_nor_the_findings_and_prints_nothing_found(
    desk, data_root, leave, reproduction
):
    planted = ["Ottoline Varga", "Billie", "ovarga" + "@" + STUDIO, BODY_LINE, "555-014-2368"]
    in_a_mail(reproduction, f"{planted[0]} and {planted[1]} wrote from {planted[2]}.")
    in_a_page(reproduction, f"{planted[3]}, as {planted[4]} was told.")
    left = leave(reproduction)
    desk.diagnose(left)

    ran = desk.release()

    # `refused` asserts that the released directory isn't there: no findings, no reproduction.
    notes = desk.refused(ran, names=1, addresses=1, quotes=1, digits=1, **{"one-word-names": 1})
    assert (desk.private / "check-notes.md").is_relative_to(data_root.path)
    for found in planted:
        assert found.lower() in notes.lower()
        assert found.lower() not in (ran.out + ran.err).lower()
    # What the session left is kept for the owner, as it left it.
    assert files_under(desk.private / "output") == files_under(left)


def test_findings_that_fail_a_rule_keep_a_clean_reproduction_from_being_released(
    desk, leave, reproduction, well_formed
):
    well_formed["summary"] = "Ottoline Varga and the larder share a page."
    desk.diagnose(leave(reproduction))

    desk.refused(desk.release(), names=1)


def test_one_refused_reproduction_keeps_the_others_from_being_released(
    desk, leave, reproduction, well_formed
):
    well_formed["findings"][1].update(reproduction="f2", no_reproduction=None)
    left = leave(reproduction)
    for path, text in reproduction.items():
        target = left / "reproductions" / "f2" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (left / "reproductions/f2/expectation.md").write_text("Ask Ottoline Varga.\n")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), names=1)

    assert "- reproductions/f2/expectation.md: the name 'ottoline varga'" in notes


def test_two_clean_reproductions_are_released_each_for_its_finding(
    desk, leave, reproduction, well_formed
):
    well_formed["findings"][1].update(reproduction="f2", no_reproduction=None)
    left = leave(reproduction)
    for path, text in reproduction.items():
        target = left / "reproductions" / "f2" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    desk.diagnose(left)

    ran = desk.release()

    assert ran.code == 0
    assert "reproductions: 2, untested\n" in ran.out
    assert "payloads: 6\n" in ran.out
    assert sorted(p.name for p in (desk.released / "reproductions").iterdir()) == ["f1", "f2"]
    for finding in ("f1", "f2"):
        record = (desk.released / "reproductions" / finding / "reproduction.json").read_text()
        assert json.loads(record)["finding"] == finding


# The session: a reproduction that isn't in form resumes it (`pa-eval diagnose`).


@pytest.fixture
def diagnose(data_root: DataRoot, cut, referral: Path, pa_eval) -> Callable[..., Any]:
    def run(*extra: object, agent: str = "claude") -> Any:
        return pa_eval(
            *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
            *("--out", data_root.diagnoses, "--agent", agent, *extra),
        )

    return run


def test_a_diagnosis_keeps_the_reproduction_its_session_left_for_the_owner_alone(
    data_root: DataRoot, diagnose, leave, reproduction
):
    ran = diagnose("--leaves", leave(reproduction), agent="scripted")

    assert ran.code == 0
    assert "resumes: 0" in ran.out
    left = data_root.diagnoses / NAME / "output" / "reproductions"
    assert files_under(left) == sorted(f"f1/{path}" for path in reproduction)
    for path in (left, *left.rglob("*")):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


NOT_IN_FORM: dict[str, tuple[dict[str, str | None], str]] = {
    "a reproduction over a cap": (
        {f"payloads/gmail/more-{number}.eml": mail(number) for number in range(38)},
        "- output/reproductions/f1/payloads: 41 payloads, over the cap of 40",
    ),
    "a file a reproduction doesn't hold": (
        {"gooseberry.txt": "The marmalade order.\n"},
        "- output/reproductions/f1/gooseberry.txt: an unexpected file",
    ),
    "no line of expectation": (
        {"expectation.md": None},
        "- output/reproductions/f1/expectation.md: missing",
    ),
    "questions that aren't an eval set": (
        {"questions.json": asked(category="gooseberry")},
        "- output/reproductions/f1/questions.json: questions.0.category: Input should be ",
    ),
    "evidence that names no payload": (
        {"questions.json": asked(evidence=["gmail/gooseberry.eml"])},
        "- output/reproductions/f1/questions.json: questions.0.evidence: 'gmail/gooseberry.eml' "
        "isn't a payload of the reproduction",
    ),
    "an owner who isn't the fictional one": (
        {"questions.json": json.dumps({**QUESTIONS, "owner": {**OWNER, "name": "Goose Berry"}})},
        "- output/reproductions/f1/questions.json: owner: isn't the synthetic corpus's "
        "fictional owner",
    ),
    "a payload that quarantines": (
        {"payloads/gmail/order.eml": ORDER.replace("Message-ID", "X-Gooseberry")},
        "- output/reproductions/f1/payloads/gmail/order.eml: quarantined: email has no "
        "Message-ID header",
    ),
    "a wiki that fails lint": (
        {"wiki/people/pru-tanager.md": PERSON.replace(" {ep:gmail/order.eml}", "")},
        "- output/reproductions/f1/wiki/people/pru-tanager.md:12: ",
    ),
    "a finding whose reproduction isn't there": (
        dict.fromkeys(CLEAN_PATHS),
        "- output/findings.json: f1.reproduction: there is no reproductions/f1",
    ),
}
"""Changes to the clean reproduction's files (None takes one away) that leave it out of form,
each with what the session is told is wrong."""


@pytest.mark.parametrize(("changes", "wrong"), NOT_IN_FORM.values(), ids=NOT_IN_FORM.keys())
def test_a_reproduction_that_isnt_in_form_resumes_the_session_with_what_is_wrong(
    data_root: DataRoot,
    diagnose,
    leave,
    reproduction,
    claude_stand_in,
    changes: dict[str, str | None],
    wrong: str,
):
    changed = {path: text for path, text in (reproduction | changes).items() if text is not None}
    stand_in = claude_stand_in({"leaves": leave(changed)}, {"leaves": leave(reproduction)})

    ran = diagnose()

    assert ran.code == 0
    _, resumed = stand_in.seen()
    assert "--resume" in resumed["argv"]
    assert wrong in resumed["prompt"]
    # What it left the second time is the diagnosis.
    diagnosis = data_root.diagnoses / NAME
    assert files_under(diagnosis / "output" / "reproductions") == sorted(
        f"f1/{path}" for path in reproduction
    )
    assert json.loads((diagnosis / "session.json").read_text())["resumes"] == 1
    assert "resumes: 1" in ran.out


def test_a_reproduction_still_out_of_form_after_two_resumes_fails_the_diagnosis(
    data_root: DataRoot, diagnose, leave, reproduction, claude_stand_in
):
    reproduction["payloads/gmail/order.eml"] = ORDER.replace("Message-ID", "X-Gooseberry")
    stand_in = claude_stand_in({"leaves": leave(reproduction)})

    ran = diagnose()

    assert ran.code == 1
    assert len(stand_in.seen()) == 3
    assert "left an output that isn't in form (1 problems), and was resumed 2 times" in ran.err
    diagnosis = data_root.diagnoses / NAME
    assert not (diagnosis / "session.json").exists()
    assert (
        "quarantined: email has no Message-ID header"
        in (diagnosis / "output-problems.md").read_text()
    )


def test_a_reproduction_that_holds_something_real_resumes_nothing(
    data_root: DataRoot, diagnose, leave, reproduction, claude_stand_in
):
    # What a reproduction may not say is the release check's to find, where the session isn't.
    in_a_mail(reproduction, "Ottoline Varga packed it.")
    stand_in = claude_stand_in({"leaves": leave(reproduction)})

    ran = diagnose()

    assert ran.code == 0
    assert len(stand_in.seen()) == 1


def test_a_reproduction_refused_for_a_name_from_the_case_is_released_once_a_second_pass_renames_it(
    data_root: DataRoot, cut, referral: Path, parts, desk, leave, reproduction, pa_eval
):
    renamed = leave(reproduction)
    where = in_a_mail(reproduction, "Ask Billie about the jars.")
    private = desk.diagnose(leave(reproduction))
    notes = desk.refused(desk.release(), **{"one-word-names": 1})
    assert f"- reproductions/f1/{where}: the name 'Billie'" in notes

    revised = pa_eval(
        *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
        *parts.arguments(),
        *("--out", data_root.diagnoses, "--revise", private / "output"),
        *("--notes", private / "check-notes.md", "--agent", "scripted", "--leaves", renamed),
    )
    ran = desk.release()

    assert (revised.code, ran.code) == (0, 0)
    # The revision's reproduction is released as any is, resolved to its own payloads' episodes.
    released = desk.released / "reproductions" / "f1"
    assert (released / "payloads" / "gmail" / "order.eml").read_text() == ORDER
    record = json.loads((released / "reproduction.json").read_text())
    assert sorted(record["episodes"]) == [
        "gmail/order.eml",
        "icloud_calendar/pickup.ics",
        "obsidian/2026-09-21.md",
    ]
    assert json.loads((desk.released / "release.json").read_text())["revisions"] == 1
    assert "reproductions: 1, untested\n" in ran.out
    # The refused reproduction stays with the private analysis, and nothing of it is released.
    kept = private / "refused" / "1" / "output" / "reproductions" / "f1"
    assert "Ask Billie about the jars." in (kept / "payloads" / "gmail" / "order.eml").read_text()
    assert files_under(kept) == sorted(reproduction)
    assert not [
        path for path in desk.released.rglob("*") if path.is_file() and "Billie" in path.read_text()
    ]
