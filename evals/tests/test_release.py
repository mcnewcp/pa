"""The release check (`pa-eval release`): a diagnosis's findings reach a development session
only when they pass every rule, and a refusal says which rule failed and how often, never what
it found.

Each test makes a diagnosis with a scripted diagnostician over a synthetic cut, then runs the
release. The commands run through `pa_eval` (conftest), which holds them to printing nothing a
session wrote; `gooseberry` marks what a test plants itself."""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pa_home.config import DataRoot
from pa_home.cut import Cut

NAME = "dreamed-drop"
"""The diagnosis the `desk` (conftest) makes and releases."""


def modes(top: Path) -> dict[str, int]:
    """The permission bits of `top` and of everything under it, by relative path."""
    return {
        str(path.relative_to(top)): stat.S_IMODE(path.lstat().st_mode)
        for path in (top, *top.rglob("*"))
    }


def test_well_formed_findings_are_released_with_a_record_of_the_release(
    desk, cut: Cut, leavings, well_formed
):
    left = leavings()
    desk.diagnose(left)

    ran = desk.release()

    assert ran.code == 0
    # The released directory holds the findings and the record, and nothing else.
    assert sorted(p.name for p in desk.released.iterdir()) == ["findings.json", "release.json"]
    assert json.loads((desk.released / "findings.json").read_text()) == well_formed
    record = json.loads((desk.released / "release.json").read_text())
    commit = record.pop("commit")
    assert re.fullmatch(r"[0-9a-f]{40}( \(with uncommitted changes\))?", commit)
    assert record == {
        "name": NAME,
        "cut": cut.name,
        "findings": 2,
        "kinds": {"fault": 1, "noise": 1},
        "checks": {
            **{"shape": 0, "schema": 0, "paths": 0, "episodes": 0, "questions": 0, "labels": 0},
            **{"names": 0, "addresses": 0, "quotes": 0, "digits": 0},
        },
    }
    # The private analysis stays under the data root, for the owner alone.
    assert (desk.private / "output" / "analysis.md").read_text() == (
        left / "analysis.md"
    ).read_text()
    assert not (desk.released / "analysis.md").exists()
    # It prints the name, the released directory, and counts.
    assert f"release: {NAME}: released" in ran.out
    assert f"released: {desk.released}" in ran.out
    assert "findings: 2 (1 fault, 1 noise)" in ran.out
    for rule in (
        *("shape", "schema", "paths", "episodes", "questions", "labels"),
        *("names", "addresses", "quotes", "digits"),
    ):
        assert f"check {rule}: 0\n" in ran.out


def test_a_release_leaves_the_diagnosis_readable_by_the_owner_alone(desk, leavings):
    private = desk.diagnose(leavings())
    # As a copy pulled from another machine might arrive.
    for path in (private, *private.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)

    assert desk.release().code == 0

    assert modes(private) == {
        ".": 0o700,
        "referral.md": 0o600,
        "output": 0o700,
        "output/analysis.md": 0o600,
        "output/findings.json": 0o600,
        "reply.md": 0o600,
        "session.json": 0o600,
    }


def test_released_findings_are_written_afresh_from_the_schemas_fields(desk, leavings, well_formed):
    # JSON lets a key come twice, and the last one counts. Only what the schema read is
    # released, never the file the session wrote.
    written = json.dumps(well_formed, indent=2).replace(
        '"answered": "partly",', '"answered": "gooseberry",\n  "answered": "partly",', 1
    )
    assert "gooseberry" in written
    desk.diagnose(leavings(written))

    assert desk.release().code == 0

    released = (desk.released / "findings.json").read_text()
    assert "gooseberry" not in released
    assert json.loads(released) == well_formed


# A refusal.


def first(findings: dict[str, Any], **fields: Any) -> None:
    findings["findings"][0].update(fields)


OFF_THE_SCHEMA: dict[str, Callable[[dict[str, Any]], Any]] = {
    "a field the schema doesn't know": lambda f: first(f, gooseberry="jam"),
    "a top-level field the schema doesn't know": lambda f: f.update(gooseberry="jam"),
    "a kind off the list": lambda f: first(f, kind="gooseberry"),
    "a component off the list": lambda f: first(f, component="gooseberry"),
    "a confidence off the list": lambda f: first(f, confidence="gooseberry"),
    "an answer off the list": lambda f: f.update(answered="gooseberry"),
    "a question named by more than its opaque id": lambda f: first(f, questions=["gooseberry"]),
    "an episode named by more than its id": lambda f: first(f, episodes=["gmail_gooseberry"]),
    "a label that isn't one": lambda f: first(f, runs={"Goose Berry": [1, 2]}),
    "more runs affected than asked": lambda f: first(f, runs={"dreamed": [7, 6]}),
    "an id that isn't a finding's": lambda f: first(f, id="gooseberry"),
    "two findings with one id": lambda f: first(f, id="f2"),
    "no mechanism": lambda f: f["findings"][0].pop("mechanism"),
    "no summary": lambda f: f.pop("summary"),
    "a reproduction, which the schema doesn't hold yet": lambda f: first(f, reproduction="f1"),
}


@pytest.mark.parametrize("change", OFF_THE_SCHEMA.values(), ids=OFF_THE_SCHEMA.keys())
def test_findings_off_the_schema_are_refused(
    desk, leavings, well_formed, change: Callable[[dict[str, Any]], Any]
):
    change(well_formed)
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), schema=1)

    # The notes say where, for the owner.
    assert "## schema: 1" in notes
    assert "- findings.json: " in notes


def test_findings_that_arent_json_are_refused(desk, leavings):
    desk.diagnose(leavings("The gooseberry findings, in prose.\n"))

    desk.refused(desk.release(), schema=1)


OVER_A_CAP: dict[str, Callable[[dict[str, Any]], Any]] = {
    "a mechanism over 800 characters": lambda f: first(f, mechanism="wombat " * 114 + "abc"),
    "a suggestion over 800 characters": lambda f: first(f, suggestion="wombat " * 114 + "abc"),
    "a summary over 600 characters": lambda f: f.update(summary="wombat " * 85 + "abcdef"),
    "more than 10 findings": lambda f: f.update(
        findings=[{**f["findings"][0], "id": f"f{n}"} for n in range(1, 12)]
    ),
}


@pytest.mark.parametrize("change", OVER_A_CAP.values(), ids=OVER_A_CAP.keys())
def test_findings_over_a_length_or_count_cap_are_refused(
    desk, leavings, well_formed, change: Callable[[dict[str, Any]], Any]
):
    change(well_formed)
    desk.diagnose(leavings(well_formed))

    desk.refused(desk.release(), schema=1)


def test_findings_at_every_cap_are_released(desk, leavings, well_formed):
    well_formed["summary"] = "s" * 600
    first(well_formed, mechanism="m" * 800, suggestion="g" * 800)
    well_formed["findings"] = [{**well_formed["findings"][0], "id": f"f{n}"} for n in range(1, 11)]
    desk.diagnose(leavings(well_formed))

    ran = desk.release()

    assert ran.code == 0
    assert "findings: 10 (10 fault)" in ran.out
    assert json.loads((desk.released / "findings.json").read_text()) == well_formed


@pytest.mark.parametrize(
    "path",
    [
        "gooseberry/nowhere.py",
        "librarian/gooseberry.md",
        "/etc/hostname",
        "../gooseberry.py",
        "librarian/../../gooseberry",
        # There, and not the repository's: git doesn't track it.
        ".venv/pyvenv.cfg",
        ".git/HEAD",
        ".",
    ],
)
def test_a_path_that_isnt_in_the_repository_is_refused(desk, leavings, well_formed, path: str):
    first(well_formed, code=["librarian/CLAUDE.md", path])
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), paths=1)

    assert f"- findings.json: f1.code: {path!r} isn't in the repository" in notes


def test_a_file_and_a_directory_in_the_repository_pass(desk, leavings, well_formed):
    first(well_formed, code=["evals/src/pa_evals/diagnosis.py", "librarian", "agent/"])
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    released = json.loads((desk.released / "findings.json").read_text())
    assert released["findings"][0]["code"] == [
        "evals/src/pa_evals/diagnosis.py",
        "librarian",
        "agent/",
    ]


def test_an_unexpected_file_is_refused(desk, leavings):
    desk.diagnose(leavings(extra={"gooseberry.txt": "Zebulon's marmalade recipe.\n"}))

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/gooseberry.txt: an unexpected file" in notes


def test_an_unexpected_directory_is_refused(desk, leavings):
    left = leavings()
    (left / "gooseberry").mkdir()
    (left / "gooseberry" / "findings.json").write_text("{}")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/gooseberry: an unexpected directory" in notes


@pytest.mark.parametrize("name", ["analysis.md", "findings.json"])
def test_a_link_in_place_of_a_file_is_refused_and_never_followed(
    tmp_path: Path, desk, leavings, name: str
):
    # The link leads to a file that would pass, were it read.
    left = leavings()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (left / name).rename(elsewhere / name)
    (left / name).symlink_to(elsewhere / name)
    desk.diagnose(left)
    assert (desk.private / "output" / name).is_symlink()

    notes = desk.refused(desk.release(), shape=1)

    assert f"- output/{name}: a link in place of a file" in notes
    # The link's target is left as it was: a release changes no file outside the diagnosis.
    assert stat.S_IMODE((elsewhere / name).stat().st_mode) != 0o600


def test_an_output_that_is_a_link_is_refused(tmp_path: Path, desk, leavings):
    private = desk.diagnose(leavings())
    elsewhere = tmp_path / "elsewhere"
    (private / "output").rename(elsewhere)
    (private / "output").symlink_to(elsewhere, target_is_directory=True)

    notes = desk.refused(desk.release(), shape=1)

    assert "- output: isn't a directory" in notes


@pytest.mark.parametrize("name", ["analysis.md", "findings.json"])
def test_a_missing_file_is_refused(desk, leavings, name: str):
    left = leavings()
    (left / name).unlink()
    desk.diagnose(left)

    notes = desk.refused(desk.release(), shape=1)

    assert f"- output/{name}: missing" in notes


def test_a_file_over_its_size_cap_is_refused(desk, leavings):
    left = leavings()
    (left / "analysis.md").write_text("The marmalade invoice.\n" * 10_000)
    desk.diagnose(left)

    desk.refused(desk.release(), shape=1)


def test_a_file_that_isnt_text_is_refused(desk, leavings):
    left = leavings()
    (left / "analysis.md").write_bytes(b"\xff\xfe\x00marmalade")
    desk.diagnose(left)

    notes = desk.refused(desk.release(), shape=1)

    assert "- output/analysis.md: not UTF-8 text" in notes


def test_every_rule_that_fails_is_counted_and_the_private_analysis_is_kept(
    desk, leavings, well_formed
):
    first(well_formed, code=["gooseberry/one.py", "gooseberry/two.py"])
    left = leavings(well_formed, extra={"gooseberry.txt": "jam\n"})
    desk.diagnose(left)

    desk.refused(desk.release(), shape=1, paths=2)

    # A refused diagnosis still tells the owner something.
    assert (desk.private / "output" / "analysis.md").read_text() == (
        left / "analysis.md"
    ).read_text()


# Where a release may be written.

INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-release-test-never-written"


def test_a_released_directory_inside_the_repository_is_refused(
    desk, leavings, monkeypatch: pytest.MonkeyPatch
):
    desk.diagnose(leavings())
    monkeypatch.setenv("PA_RELEASED_DIR", str(INSIDE))

    ran = desk.release()

    assert ran.code == 1
    assert f"PA_RELEASED_DIR ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()


def test_a_released_directory_linked_into_the_repository_is_refused(desk, leavings):
    desk.diagnose(leavings())
    desk.released.parent.mkdir()
    desk.released.symlink_to(INSIDE, target_is_directory=True)

    ran = desk.release()

    assert ran.code == 1
    assert f"the released directory ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()


@pytest.mark.parametrize("where", ["released", "evals/released", "."])
def test_a_released_directory_inside_the_data_root_is_refused(
    desk, data_root: DataRoot, leavings, monkeypatch: pytest.MonkeyPatch, where: str
):
    desk.diagnose(leavings())
    monkeypatch.setenv("PA_RELEASED_DIR", str(data_root.path / where))

    ran = desk.release()

    assert ran.code == 1
    assert "PA_RELEASED_DIR" in ran.err and "must be apart" in ran.err
    assert not (data_root.path / where / NAME).exists()


def test_a_released_directory_that_holds_the_data_root_is_refused(
    desk, data_root: DataRoot, leavings, monkeypatch: pytest.MonkeyPatch
):
    desk.diagnose(leavings())
    monkeypatch.setenv("PA_RELEASED_DIR", str(data_root.path.parent))

    ran = desk.release()

    assert ran.code == 1
    assert "must be apart" in ran.err
    assert not (data_root.path.parent / NAME).exists()


def test_a_data_root_inside_the_repository_is_refused(
    desk, leavings, monkeypatch: pytest.MonkeyPatch
):
    desk.diagnose(leavings())
    monkeypatch.setenv("PA_DATA_DIR", str(INSIDE))

    ran = desk.release()

    assert ran.code == 1
    assert f"PA_DATA_DIR ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()
    assert not desk.released.exists()


def test_a_private_directory_linked_into_the_repository_is_refused(desk):
    desk.private.parent.mkdir(parents=True)
    desk.private.symlink_to(INSIDE, target_is_directory=True)

    ran = desk.release()

    assert ran.code == 1
    assert f"the private directory ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()
    assert not desk.released.exists()


def test_a_private_directory_linked_out_of_the_data_root_is_refused(tmp_path: Path, desk, leavings):
    private = desk.diagnose(leavings())
    private.rename(tmp_path / "elsewhere")
    private.symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    ran = desk.release()

    assert ran.code == 1
    assert "isn't under the data root" in ran.err
    assert not desk.released.exists()


def test_a_case_inside_the_repository_is_refused(desk, leavings, pa_eval):
    desk.diagnose(leavings())

    ran = pa_eval("release", "--name", NAME, "--cut", INSIDE)

    assert ran.code == 1
    assert f"--cut ({INSIDE}) is inside the repository" in ran.err
    assert not desk.released.exists()


# No release is written over, and a diagnosis is checked against the case it read.


def test_a_name_already_released_is_refused(desk, leavings):
    desk.diagnose(leavings())
    desk.released.mkdir(parents=True)
    (desk.released / "findings.json").write_text("an earlier release\n")

    ran = desk.release()

    assert ran.code == 1
    assert f"a diagnosis named {NAME} is already released" in ran.err
    assert [p.name for p in desk.released.iterdir()] == ["findings.json"]
    assert (desk.released / "findings.json").read_text() == "an earlier release\n"


def test_a_diagnosis_is_released_once(desk, leavings):
    desk.diagnose(leavings())
    assert desk.release().code == 0
    released = (desk.released / "release.json").read_text()

    ran = desk.release()

    assert ran.code == 1
    assert "already released" in ran.err
    assert (desk.released / "release.json").read_text() == released


def test_a_name_with_no_diagnosis_is_refused(desk):
    ran = desk.release()

    assert ran.code == 1
    assert f"there is no diagnosis named {NAME}" in ran.err
    assert not desk.released.exists()


def test_a_diagnosis_is_checked_against_the_cut_it_read(desk, leavings):
    private = desk.diagnose(leavings())
    session = json.loads((private / "session.json").read_text())
    (private / "session.json").write_text(json.dumps({**session, "cut": "20250101T000000Z"}))

    ran = desk.release()

    assert ran.code == 1
    assert "read the cut 20250101T000000Z" in ran.err
    assert not desk.released.exists()


def test_a_session_record_that_isnt_one_is_refused_without_quoting_it(desk, leavings):
    private = desk.diagnose(leavings())
    session = json.loads((private / "session.json").read_text())
    (private / "session.json").write_text(json.dumps({**session, "commit": "gooseberry"}))

    ran = desk.release()

    assert ran.code == 1
    assert "isn't a session's record" in ran.err
    assert not desk.released.exists()
