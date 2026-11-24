"""A refused diagnosis is revised (`pa-eval diagnose --revise OUTPUT --notes FILE`): a second
session is given the output the release check refused and the check's notes, over the same
referral and case, and leaves a revised output in the diagnosis's directory, where every pass
is kept.

The diagnosis here is refused for a path that isn't in the repository, which the check's notes
quote: the path holds a word of conftest's `SALT`, so no command may print it."""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pa_home.config import DataRoot
from pa_home.cut import Cut

NAME = "dreamed-drop"
SLIPPED = "core/src/pa_core/gooseberry.py"
"""A path no file in the repository has, which a finding blames."""


def modes(top: Path) -> set[tuple[str, int]]:
    """Whether each thing under `top` is a file, with its mode."""
    return {
        ("file" if path.is_file() else "directory", stat.S_IMODE(path.lstat().st_mode))
        for path in (top, *top.rglob("*"))
    }


PRIVATE = {("directory", 0o700), ("file", 0o600)}


@pytest.fixture
def slip(leavings, well_formed) -> Callable[[str], Path]:
    """Makes what a session leaves that the release check refuses: well-formed findings, one of
    which blames the given path, which isn't in the repository."""

    def leave(path: str) -> Path:
        findings = json.loads(json.dumps(well_formed))
        findings["findings"][0]["code"] = [path]
        return leavings(findings)

    return leave


@pytest.fixture
def slipped(slip) -> Path:
    return slip(SLIPPED)


@pytest.fixture
def refused(desk, slipped: Path) -> Path:
    """The private directory of a diagnosis the release check has refused."""
    private = desk.diagnose(slipped)
    notes = desk.refused(desk.release(), paths=1)
    assert SLIPPED in notes
    return private


@pytest.fixture
def revise(data_root: DataRoot, cut: Cut, referral: Path, parts, pa_eval) -> Callable[..., Any]:
    """Runs the second pass over the diagnosis `NAME`: by default over the output and the notes
    in its own directory, and the case's parts as they are when it runs."""

    def run(*extra: object, output: Path | None = None, notes: Path | None = None) -> Any:
        private = data_root.diagnoses / NAME
        return pa_eval(
            *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
            *parts.arguments(),
            *("--out", data_root.diagnoses),
            *("--revise", output or private / "output"),
            *("--notes", notes or private / "check-notes.md"),
            *extra,
        )

    return run


def blamed(findings: Path) -> list[str]:
    """The paths the first finding in a findings file blames."""
    return json.loads(findings.read_text())["findings"][0]["code"]


def test_a_second_pass_leaves_a_revised_output_and_keeps_the_refused_one_with_its_notes(
    desk, refused: Path, revise, leavings, well_formed
):
    notes = (refused / "check-notes.md").read_text()

    ran = revise("--agent", "scripted", "--leaves", leavings())

    assert ran.code == 0
    # The revision is the diagnosis's output now, in the shape an output has.
    assert sorted(path.name for path in (refused / "output").iterdir()) == [
        "analysis.md",
        "findings.json",
    ]
    assert json.loads((refused / "output" / "findings.json").read_text()) == well_formed
    # The pass that was refused is kept beside it, whole: its output and the check's notes.
    kept = refused / "refused" / "1"
    assert blamed(kept / "output" / "findings.json") == [SLIPPED]
    assert (kept / "output" / "analysis.md").is_file()
    assert (kept / "check-notes.md").read_text() == notes
    assert not (refused / "check-notes.md").exists()
    assert modes(refused) == PRIVATE
    # The revision is counted, where the diagnosis is recorded and in what is printed.
    assert json.loads((refused / "session.json").read_text())["revisions"] == 1
    assert json.loads((kept / "session.json").read_text())["revisions"] == 0
    assert ran.out.splitlines()[-3:] == ["resumes: 0", "revisions: 1", f"left: {refused}"]


def test_the_second_pass_is_a_new_session_given_the_refused_output_and_the_notes(
    refused: Path, revise, leavings, claude_stand_in
):
    notes = (refused / "check-notes.md").read_text()
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = revise("--agent", "claude")

    assert ran.code == 0
    (seen,) = stand_in.seen()
    # A session of its own, never the refused one resumed, asked the same referral.
    assert "--resume" not in seen["argv"]
    assert "Why do two questions fail more often on the dreamed wiki?" in seen["prompt"]
    # It finds the refused output where it writes, to change where it is.
    assert sorted(seen["found"]) == ["analysis.md", "findings.json"]
    assert json.loads(seen["found"]["findings.json"])["findings"][0]["code"] == [SLIPPED]
    # It is told what the check found, and where, and how a revision is made.
    assert notes.strip() in seen["prompt"]
    assert f"- findings.json: f1.code: '{SLIPPED}' isn't in the repository" in seen["prompt"]
    instructions = " ".join(seen["claude_md"].split())
    assert "Rename what the notes name" in instructions
    assert "Rewrite what they quote" in instructions
    assert "Change nothing else" in instructions
    # The same case, read-only, and the output directory the only place it may write.
    assert seen["output"] == str(refused / "output")
    assert seen["writable"] == []
    assert "eval-set.json" in seen["case_files"]


def test_notes_too_long_to_tell_whole_are_told_from_the_top_and_kept_whole(
    refused: Path, revise, leavings, claude_stand_in, tmp_path: Path
):
    """A check that found a great deal can't be told whole: the session would have no room
    left to work in."""
    found = [
        f"- findings.json: f1.code: 'core/gooseberry_{number:04}.py' isn't in the repository"
        for number in range(4000)
    ]
    long_notes = tmp_path / "long-notes.md"
    long_notes.write_text("## paths: 4000\n\n" + "\n".join(found) + "\n")
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = revise("--agent", "claude", notes=long_notes)

    assert ran.code == 0
    (seen,) = stand_in.seen()
    assert found[0] in seen["prompt"]
    assert found[-1] not in seen["prompt"]
    assert len(seen["prompt"]) < 110_000
    told = sum(line in seen["prompt"] for line in found)
    assert f"The notes go on for {4000 - told} more lines" in seen["prompt"]
    # Every line the session is told is a whole one.
    assert seen["prompt"].count("isn't in the repository") == told
    assert (refused / "refused" / "1" / "check-notes.md").read_text() == long_notes.read_text()


def test_a_clean_revision_is_released_with_its_count_of_revisions(
    desk, refused: Path, revise, leavings
):
    assert revise("--agent", "scripted", "--leaves", leavings()).code == 0

    ran = desk.release()

    assert ran.code == 0
    record = json.loads((desk.released / "release.json").read_text())
    assert record["revisions"] == 1
    # Nothing of a refused pass, or of the check's notes on it, is released.
    assert sorted(path.name for path in desk.released.iterdir()) == [
        "findings.json",
        "release.json",
    ]
    assert SLIPPED not in (desk.released / "findings.json").read_text()
    # Both passes stay with the private analysis, for the owner alone.
    assert (refused / "refused" / "1" / "check-notes.md").is_file()
    assert modes(refused) == PRIVATE


AGAIN = "core/src/pa_core/gooseberry_fool.py"
"""Another path no file in the repository has."""


def test_every_pass_of_a_diagnosis_refused_three_times_is_kept_and_it_is_revised_no_more(
    desk, refused: Path, revise, slip, leavings
):
    first = (refused / "check-notes.md").read_text()
    assert revise("--agent", "scripted", "--leaves", slip(AGAIN)).code == 0
    second = desk.refused(desk.release(), paths=1)
    assert revise("--agent", "scripted", "--leaves", slip(AGAIN)).code == 0
    third = desk.refused(desk.release(), paths=1)
    before = files(refused)

    ran = revise("--agent", "scripted", "--leaves", leavings())

    # The check is never worn down: a third revision is refused before a session starts.
    assert ran.code == 1
    assert f"the diagnosis {NAME} has been revised 2 times already" in ran.err
    assert files(refused) == before
    # Each pass's output is kept with what the check found in it.
    assert blamed(refused / "refused" / "1" / "output" / "findings.json") == [SLIPPED]
    assert (refused / "refused" / "1" / "check-notes.md").read_text() == first
    assert blamed(refused / "refused" / "2" / "output" / "findings.json") == [AGAIN]
    assert (refused / "refused" / "2" / "check-notes.md").read_text() == second
    assert blamed(refused / "output" / "findings.json") == [AGAIN]
    assert (refused / "check-notes.md").read_text() == third
    assert SLIPPED in first and SLIPPED not in second
    assert sorted(path.name for path in (refused / "refused").iterdir()) == ["1", "2"]
    assert json.loads((refused / "session.json").read_text())["revisions"] == 2
    assert modes(refused) == PRIVATE
    # And it stays unreleased.
    assert not desk.released.exists()


def files(top: Path) -> dict[str, bytes]:
    """Every file under `top`, by relative path."""
    return {
        str(path.relative_to(top)): path.read_bytes() for path in top.rglob("*") if path.is_file()
    }


INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-revision-test-never-written"
"""A path inside the checkout (git-ignored), which no test ever makes."""


@pytest.mark.parametrize(
    "which",
    [
        "another referral",
        "another case",
        "an output that isn't there",
        "notes that aren't there",
        "an output inside the repository",
        "notes inside the repository",
    ],
)
def test_a_second_pass_that_isnt_one_is_refused_before_anything_changes(
    refused: Path, revise, referral: Path, parts, leavings, tmp_path: Path, which: str
):
    before = files(refused)
    scripted = ("--agent", "scripted", "--leaves", leavings())
    nowhere = tmp_path / "nowhere"

    if which == "another referral":
        referral.write_text("Why does the brief skip Mondays?\n")
        ran, said = revise(*scripted), "answered another referral than the one given"
    elif which == "another case":
        del parts.runs["own"]
        ran, said = revise(*scripted), "read another case than the one given"
    elif which == "an output that isn't there":
        ran, said = revise(*scripted, output=nowhere / "output"), "isn't a directory"
    elif which == "notes that aren't there":
        ran, said = revise(*scripted, notes=nowhere / "check-notes.md"), "aren't a file"
    elif which == "an output inside the repository":
        ran, said = revise(*scripted, output=INSIDE / "output"), "is inside the repository"
    else:
        ran, said = revise(*scripted, notes=INSIDE / "check-notes.md"), "is inside the repository"

    assert ran.code == 1
    assert said in ran.err
    assert ran.out == ""
    assert files(refused) == before
    assert not INSIDE.exists()


def test_a_refused_output_without_the_checks_notes_isnt_a_second_pass(
    data_root: DataRoot, cut: Cut, referral: Path, parts, refused: Path, leavings, pa_eval
):
    before = files(refused)

    ran = pa_eval(
        *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
        *parts.arguments(),
        *("--out", data_root.diagnoses, "--revise", refused / "output"),
        *("--agent", "scripted", "--leaves", leavings()),
    )

    assert ran.code == 1
    assert "a second pass needs both --revise DIR and --notes FILE" in ran.err
    assert files(refused) == before


def test_only_a_diagnosis_that_was_left_whole_is_revised(
    data_root: DataRoot, refused: Path, revise, leavings
):
    """One whose session failed left no record of itself, and nothing to check or revise."""
    (refused / "session.json").unlink()
    before = files(refused)

    ran = revise("--agent", "scripted", "--leaves", leavings())

    assert ran.code == 1
    assert f"{refused} isn't a diagnosis: it has no session.json" in ran.err
    assert files(refused) == before


def test_a_revision_left_out_of_form_is_resumed_as_a_first_pass_is(
    refused: Path, revise, leavings, well_formed, claude_stand_in
):
    no_analysis = leavings()
    (no_analysis / "analysis.md").unlink()
    stand_in = claude_stand_in({"leaves": no_analysis}, {"leaves": leavings()})

    ran = revise("--agent", "claude")

    assert ran.code == 0
    revising, resumed = stand_in.seen()
    assert "--resume" not in revising["argv"]
    assert resumed["argv"][resumed["argv"].index("--resume") + 1] == "s-7c1"
    assert "- output/analysis.md: missing" in resumed["prompt"]
    # The resumed session is told what is out of form, and never the check's notes again.
    assert SLIPPED not in resumed["prompt"]
    assert json.loads((refused / "output" / "findings.json").read_text()) == well_formed
    session = json.loads((refused / "session.json").read_text())
    assert (session["revisions"], session["resumes"]) == (1, 1)
    assert ran.out.splitlines()[-3:-1] == ["resumes: 1", "revisions: 1"]


def test_a_revision_whose_session_fails_leaves_nothing_to_release(
    desk, refused: Path, revise, leavings, claude_stand_in
):
    notes = (refused / "check-notes.md").read_text()
    claude_stand_in(
        {
            "leaves": leavings(),
            "result": {"is_error": True, "subtype": "error_max_turns", "result": None},
        }
    )

    ran = revise("--agent", "claude")

    assert ran.code == 1
    assert f"the diagnostician's session failed, so the diagnosis {NAME} failed" in ran.err
    # The refused pass is kept whole, and what the failed session left is no diagnosis: it has
    # no record, so it can't be released.
    kept = refused / "refused" / "1"
    assert blamed(kept / "output" / "findings.json") == [SLIPPED]
    assert (kept / "check-notes.md").read_text() == notes
    assert json.loads((kept / "session.json").read_text())["revisions"] == 0
    assert not (refused / "session.json").exists()
    assert "error_max_turns" in (refused / "session-error.txt").read_text()
    assert modes(refused) == PRIVATE
    released = desk.release()
    assert released.code == 1
    assert "isn't a diagnosis: it has no session.json" in released.err
    assert not desk.released.exists()


def test_a_revised_diagnosis_counts_what_every_pass_cost(
    data_root: DataRoot, cut: Cut, referral: Path, parts, desk, slipped, leavings, pa_eval, revise,
    claude_stand_in,
):  # fmt: skip
    claude_stand_in({"leaves": slipped}, {"leaves": leavings()})
    first = pa_eval(
        *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
        *parts.arguments(),
        *("--out", data_root.diagnoses, "--agent", "claude"),
    )
    desk.refused(desk.release(), paths=1)

    ran = revise("--agent", "claude")

    assert (first.code, ran.code) == (0, 0)
    assert "session: $1.25, 10 turns" in first.out
    assert "revisions: 0" in first.out
    # The diagnosis's figures are both sessions' together; the refused pass keeps its own.
    assert "session: $2.50, 20 turns" in ran.out
    session = json.loads((desk.private / "session.json").read_text())
    assert (session["cost_usd"], session["turns"], session["duration_ms"]) == (2.5, 20, 120000)
    kept = json.loads((desk.private / "refused" / "1" / "session.json").read_text())
    assert (kept["cost_usd"], kept["turns"]) == (1.25, 10)


def test_a_scripted_session_leaves_what_it_is_told_to_when_it_revises(
    refused: Path, revise, slipped: Path, leavings, well_formed
):
    """A script gives every pass the same options, so a scripted revision has its own."""
    ran = revise("--agent", "scripted", "--leaves", slipped, "--revised", leavings())

    assert ran.code == 0
    assert json.loads((refused / "output" / "findings.json").read_text()) == well_formed
