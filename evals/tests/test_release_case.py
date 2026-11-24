"""The release check over a whole case (`pa-eval release`): what the eval set, the runs, a
second wiki, a wiki's history, and an earlier private analysis hold is refused in findings as
what the cut holds is, and a finding counts runs only under the labels the case gives.

The case is synthetic. This file is in the repository, and a run of words the repository holds
passes the check, so each of the case's sentences is written backwards here (`forwards` turns
it round)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pa_home.config import DataRoot
from pa_home.cut import Cut

NAME = "dreamed-drop"


def forwards(backwards: str) -> str:
    """The words of `backwards` in the order the case holds them."""
    return " ".join(reversed(backwards.split()))


QUESTION = forwards("last glaze wombat the did bowl heron Which")
EXPECTED = forwards("rim sgraffito the with one tall The")
ANSWER = forwards("pot marmalade small the glazed wombat The")
REASONING = forwards("bowl a never and pot a names answer The")
EARLIER = forwards("merge page heron the blamed look earlier The")
HISTORY_LINE = forwards("jars three for owes still wombat the Open")

PERSON = """\
---
type: person
aliases: []
status: active
summary: Fires the kiln.
updated: 2026-09-22
---
# Perpetua Nightingale

## Current state

## Open loops

## Notes
"""
HISTORY = f"""\
commit 0a1b2c3d4e5f60718293a4b5c6d7e8f901234567
date 2026-09-30T03:00:00+00:00

dreaming run: 1 candidates, 1 applied, 0 proposed, 0 dismissed

diff --git a/people/ignatius-thistlewood.md b/people/ignatius-thistlewood.md
deleted file mode 100644
--- a/people/ignatius-thistlewood.md
+++ /dev/null
@@ -1,3 +0,0 @@
-# Iggy Thistledown
-
-- {HISTORY_LINE}.
"""


@pytest.fixture
def parts(parts, tmp_path: Path, data_root: DataRoot, cut: Cut, write_eval_set, lay_runs):
    """A whole case: the eval set, two sets of runs, a dreamed copy of the cut's wiki with its
    history, and an earlier diagnosis. Each holds a sentence, or a name, of its own."""
    assert cut.wiki is not None
    parts.eval_set = write_eval_set(
        {"r07": (QUESTION, EXPECTED), "r12": ("Who glazed it?", "The grocer.")}
    )
    parts.runs["dreamed"] = [lay_runs("dreamed-again", 6, answer=ANSWER, reasoning=REASONING)]
    dreamed = tmp_path / "copies" / "dreamed" / "wiki"
    shutil.copytree(cut.wiki, dreamed)
    (dreamed / "people").mkdir(exist_ok=True)
    (dreamed / "people" / "perpetua-nightingale.md").write_text(PERSON)
    parts.wikis["dreamed"] = dreamed
    history = tmp_path / "histories" / "dreamed"
    history.mkdir(parents=True)
    (history / "0001-0a1b2c3d4e5f.patch").write_text(HISTORY)
    parts.histories["dreamed"] = history
    earlier = data_root.diagnoses / "first-look"
    (earlier / "output").mkdir(parents=True)
    (earlier / "output" / "analysis.md").write_text(f"# The first look\n\n{EARLIER}.\n")
    parts.earlier.append(earlier)
    return parts


def first(findings: dict[str, Any], **fields: Any) -> None:
    findings["findings"][0].update(fields)


def test_findings_over_a_whole_case_are_released(desk, leavings, well_formed):
    desk.diagnose(leavings())

    ran = desk.release()

    assert ran.code == 0
    assert "check questions: 0" in ran.out and "check labels: 0" in ran.out
    assert json.loads((desk.released / "findings.json").read_text()) == well_formed


QUOTED = {
    "an eval question": (QUESTION, "the eval set's question r07"),
    "an expected answer": (EXPECTED, "the eval set's expected answer to r07"),
    "a run's answer": (ANSWER, "the answer to r07 in run 1 under dreamed"),
    "the judge's reasoning": (REASONING, "the judge's reasoning on r07 in run 1 under dreamed"),
    "an earlier private analysis": (EARLIER, "the earlier diagnosis first-look's analysis"),
    "a line only a wiki's history holds": (HISTORY_LINE, "dreamed's history"),
}


@pytest.mark.parametrize("text, held", QUOTED.values(), ids=QUOTED.keys())
def test_findings_that_quote_a_part_of_the_case_are_refused(
    desk, leavings, well_formed, text: str, held: str
):
    first(well_formed, mechanism=f"The session went wrong where it says: {text}")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), quotes=1)

    assert f"words in a row that the case holds ({held}" in notes


def test_a_name_that_only_a_second_wiki_of_the_case_holds_is_refused(desk, leavings, well_formed):
    first(well_formed, mechanism="The merge files Perpetua Nightingale under another page.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=1)

    assert "(the slug of dreamed/people/perpetua-nightingale.md)" in notes


def test_that_name_passes_a_case_without_the_second_wiki(desk, parts, leavings, well_formed):
    parts.wikis.clear()
    parts.histories.clear()
    first(well_formed, mechanism="The merge files Perpetua Nightingale under another page.")
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0


HISTORYS_NAMES = {
    "the slug of a page a run deleted": "Ignatius Thistlewood",
    "the name of that page": "Iggy Thistledown",
}


@pytest.mark.parametrize("name", HISTORYS_NAMES.values(), ids=HISTORYS_NAMES.keys())
def test_a_name_that_only_a_wikis_history_holds_is_refused(desk, leavings, well_formed, name: str):
    well_formed["summary"] = f"A dreaming run deleted the page of {name}."
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=1)

    assert "in dreamed's history" in notes


def test_a_history_with_a_run_that_changed_no_page_is_checked_like_any_other(
    desk, parts, leavings, well_formed
):
    """A wiki's first commit is an empty one (`Start the wiki`), so every real history has a
    file with no diff in it."""
    (parts.histories["dreamed"] / "0000-00112233aabb.patch").write_text(
        "commit 00112233aabbccddeeff00112233aabbccddeeff\n"
        "date 2026-09-29T03:00:00+00:00\n\nStart the wiki\n"
    )
    well_formed["summary"] = "A dreaming run deleted the page of Ignatius Thistlewood."
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=1)

    assert "in dreamed's history" in notes


# A finding's counts of runs, and the questions it names.


def test_a_count_of_runs_under_a_label_the_referral_didnt_give_is_refused(
    desk, leavings, well_formed
):
    first(well_formed, runs={"own": [0, 3], "undreamed": [1, 3]})
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), labels=1)

    assert "- findings.json: f1.runs: 'undreamed' isn't a label of a set of runs" in notes


def test_a_wikis_label_isnt_a_set_of_runs(desk, parts, leavings, well_formed):
    parts.wikis["copy"] = parts.wikis.pop("dreamed")
    parts.histories.clear()
    first(well_formed, runs={"copy": [1, 3]})
    desk.diagnose(leavings(well_formed))

    desk.refused(desk.release(), labels=1)


def test_a_count_of_more_runs_than_a_label_holds_is_refused(desk, leavings, well_formed):
    first(well_formed, runs={"own": [3, 4], "dreamed": [6, 6]})
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), labels=1)

    assert "- findings.json: f1.runs: 4 runs asked under 'own', which holds 3" in notes


def test_a_question_that_isnt_in_the_case_is_refused(desk, leavings, well_formed):
    first(well_formed, questions=["r07", "r99"])
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), questions=1)

    assert "- findings.json: f1.questions: 'r99' isn't a question in the case" in notes


def test_a_question_the_runs_asked_is_in_the_case(desk, parts, leavings):
    # No eval set: the questions are those the runs asked, r07 and r12.
    parts.eval_set = None
    desk.diagnose(leavings())

    assert desk.release().code == 0


def test_findings_over_a_cut_alone_name_no_question_and_count_no_runs(
    desk, parts, leavings, well_formed
):
    parts.eval_set = None
    parts.runs.clear()
    parts.wikis.clear()
    parts.histories.clear()
    parts.earlier.clear()
    desk.diagnose(leavings())

    # Two findings name three questions, and count runs under three labels.
    desk.refused(desk.release(), questions=3, labels=3)


# The check is given the case the session read.

OTHER_PARTS = {
    "no eval set": lambda parts: setattr(parts, "eval_set", None),
    "a set of runs fewer": lambda parts: parts.runs.pop("own"),
    "a run directory more": lambda parts: parts.runs["own"].append(parts.runs["dreamed"][0]),
    "no second wiki": lambda parts: (parts.wikis.clear(), parts.histories.clear()),
    "no history": lambda parts: parts.histories.clear(),
    "no earlier diagnosis": lambda parts: parts.earlier.clear(),
}


@pytest.mark.parametrize("change", OTHER_PARTS.values(), ids=OTHER_PARTS.keys())
def test_a_diagnosis_is_checked_against_a_case_of_the_parts_it_read(desk, parts, leavings, change):
    desk.diagnose(leavings())
    change(parts)

    ran = desk.release()

    assert ran.code == 1
    assert f"the diagnosis {NAME} read a case of other parts" in ran.err
    assert (
        "It read: an eval set; runs own (3), dreamed (6); wikis dreamed (1 runs of history); "
        "earlier first-look"
    ) in ran.err
    assert not desk.released.exists()


INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-release-case-never-written"

PARTS = {
    "--eval-set": lambda inside: ["--eval-set", inside],
    "--runs": lambda inside: ["--runs", f"more={inside}"],
    "--wiki": lambda inside: ["--wiki", f"more={inside}"],
    "--history": lambda inside: ["--history", f"more={inside}"],
    "--earlier": lambda inside: ["--earlier", inside],
}


@pytest.mark.parametrize("option", PARTS)
def test_each_part_of_the_case_is_refused_inside_the_repository_on_release(
    desk, leavings, option: str
):
    desk.diagnose(leavings())

    ran = desk.release(*PARTS[option](INSIDE))

    assert ran.code == 1
    named = option if option in ("--eval-set", "--earlier") else f"{option} more"
    assert f"{named} ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()
    assert not desk.released.exists()


def test_the_live_wiki_is_refused_as_a_wiki_of_the_case_on_release(
    desk, data_root: DataRoot, leavings
):
    desk.diagnose(leavings())

    ran = desk.release("--wiki", f"live={data_root.wiki}")

    assert ran.code == 1
    assert f"the wiki under live ({data_root.wiki}) is a live store's" in ran.err
    assert not desk.released.exists()
