"""The bounds on a diagnostician's session (`pa-eval diagnose`): a malformed output resumes it
with what is wrong, at most twice; a session out of time or turns fails the diagnosis whole;
and its model, effort, turn limit, and timeout are configuration.

The session is a stand-in `claude` on PATH (conftest's `claude_stand_in`), which does a step
each time the real launcher runs it and records what it saw."""

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


@pytest.fixture
def diagnose(data_root: DataRoot, cut: Cut, referral: Path, pa_eval) -> Callable[..., Any]:
    def run(*extra: object, agent: str = "claude") -> Any:
        return pa_eval(
            *("diagnose", "--referral", referral, "--cut", cut.path, "--name", NAME),
            *("--out", data_root.diagnoses, "--agent", agent, *extra),
        )

    return run


def modes(top: Path) -> dict[str, int]:
    """The permission bits of `top` and of everything under it, by relative path."""
    return {
        str(path.relative_to(top)): stat.S_IMODE(path.lstat().st_mode)
        for path in (top, *top.rglob("*"))
    }


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


@pytest.fixture
def malformed(leavings, well_formed) -> dict[str, tuple[Path, str]]:
    """Outputs that aren't in form, each with what its session is told is wrong."""
    off_the_schema = json.loads(json.dumps(well_formed))
    off_the_schema["findings"][0]["gooseberry"] = "jam"
    off_the_schema["summary"] = "wombat " * 100
    no_analysis = leavings()
    (no_analysis / "analysis.md").unlink()
    over_its_cap = leavings()
    (over_its_cap / "analysis.md").write_text("marmalade " * 20_001)
    return {
        "findings off the schema": (
            leavings(off_the_schema),
            "- output/findings.json: summary: String should have at most 600 characters\n"
            "- output/findings.json: findings.0.gooseberry: Extra inputs are not permitted",
        ),
        "a missing private analysis": (no_analysis, "- output/analysis.md: missing"),
        "an output over its caps": (
            over_its_cap,
            "- output/analysis.md: 200010 bytes, over its cap of 200000",
        ),
        "a file the output doesn't hold": (
            leavings(extra={"notes.txt": "The wombat notes."}),
            "- output/notes.txt: an unexpected file",
        ),
    }


@pytest.mark.parametrize("which", range(4))
def test_a_malformed_output_resumes_the_session_with_what_is_wrong(
    data_root: DataRoot, diagnose, malformed, leavings, well_formed, claude_stand_in, which: int
):
    assert len(malformed) == 4
    left, wrong = list(malformed.values())[which]
    stand_in = claude_stand_in({"leaves": left}, {"leaves": leavings()})

    ran = diagnose()

    assert ran.code == 0
    started, resumed = stand_in.seen()
    # The same session, in the project it was started in, keeping its transcript.
    assert "--resume" not in started["argv"]
    assert flag(resumed["argv"], "--resume") == "s-7c1"
    assert resumed["cwd"] == started["cwd"]
    assert "--no-session-persistence" not in started["argv"] + resumed["argv"]
    # It is told what is wrong with what it left, and nothing of the referral again.
    assert wrong in resumed["prompt"]
    assert "Why do two questions fail" not in resumed["prompt"]
    # What it left the second time is the diagnosis.
    diagnosis = data_root.diagnoses / NAME
    assert sorted(path.name for path in (diagnosis / "output").iterdir()) == [
        "analysis.md",
        "findings.json",
    ]
    assert json.loads((diagnosis / "output" / "findings.json").read_text()) == well_formed
    # The resume is counted, and the session's cost and turns are both times' together.
    session = json.loads((diagnosis / "session.json").read_text())
    assert (session["resumes"], session["cost_usd"], session["turns"]) == (1, 2.5, 20)
    assert session["duration_ms"] == 120000
    assert "session: $2.50, 20 turns" in ran.out
    assert "resumes: 1" in ran.out


def test_a_well_formed_output_resumes_nothing(
    data_root: DataRoot, diagnose, leavings, claude_stand_in
):
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = diagnose()

    assert ran.code == 0
    assert len(stand_in.seen()) == 1
    assert json.loads((data_root.diagnoses / NAME / "session.json").read_text())["resumes"] == 0
    assert "resumes: 0" in ran.out


def test_the_diagnosis_fails_when_the_output_is_still_malformed_after_two_resumes(
    data_root: DataRoot, diagnose, malformed, claude_stand_in
):
    left, _ = malformed["findings off the schema"]
    stand_in = claude_stand_in({"leaves": left})

    ran = diagnose()

    assert ran.code == 1
    # Started once and resumed twice, and no more.
    assert ["--resume" in seen["argv"] for seen in stand_in.seen()] == [False, True, True]
    diagnosis = data_root.diagnoses / NAME
    assert (
        f"the diagnostician's session left an output that isn't in form (2 problems), and was "
        f"resumed 2 times, so the diagnosis {NAME} failed"
    ) in ran.err
    assert str(diagnosis) in ran.err
    # What it left is kept for the owner alone, with what is wrong with it, and it is no
    # diagnosis to release: it has no session's record.
    assert modes(diagnosis) == {
        ".": 0o700,
        "referral.md": 0o600,
        "output": 0o700,
        "output/analysis.md": 0o600,
        "output/findings.json": 0o600,
        "output-problems.md": 0o600,
        "reply.md": 0o600,
    }
    assert "findings.0.gooseberry" in (diagnosis / "output-problems.md").read_text()


def test_a_scripted_session_is_resumed_as_a_real_one_is(
    data_root: DataRoot, diagnose, malformed, leavings, well_formed
):
    left, _ = malformed["a missing private analysis"]

    ran = diagnose("--leaves", left, "--leaves", leavings(), agent="scripted")

    assert ran.code == 0
    output = data_root.diagnoses / NAME / "output"
    assert json.loads((output / "findings.json").read_text()) == well_formed
    assert (output / "analysis.md").is_file()
    assert "resumes: 1" in ran.out


def test_a_session_that_cant_be_resumed_fails_the_diagnosis_at_once(
    data_root: DataRoot, diagnose, malformed, claude_stand_in
):
    left, _ = malformed["a missing private analysis"]
    # The CLI reported no session to resume.
    stand_in = claude_stand_in({"leaves": left, "result": {"session_id": None}})

    ran = diagnose()

    assert ran.code == 1
    assert len(stand_in.seen()) == 1
    assert "isn't in form (1 problems), and can't be resumed" in ran.err
    assert not (data_root.diagnoses / NAME / "session.json").exists()


def test_a_session_that_runs_out_of_time_fails_the_diagnosis_and_releases_nothing(
    desk, diagnose, leavings, claude_stand_in
):
    # It left a whole output, and was still working when its time ran out.
    claude_stand_in({"leaves": leavings(), "sleep": 30})

    ran = diagnose("--timeout", "0.5")

    assert ran.code == 1
    assert f"the diagnostician's session ran out of time, so the diagnosis {NAME} failed" in ran.err
    # What it left is kept under the data root, for the owner alone.
    assert modes(desk.private) == {
        ".": 0o700,
        "referral.md": 0o600,
        "output": 0o700,
        "output/analysis.md": 0o600,
        "output/findings.json": 0o600,
        "session-error.txt": 0o600,
    }
    # And none of it is released: half an answer never reads as a finding.
    released = desk.release()
    assert released.code == 1
    assert "isn't a diagnosis: it has no session.json" in released.err
    assert not desk.released.exists()


def test_a_resumed_session_that_runs_out_of_time_fails_the_diagnosis(
    data_root: DataRoot, diagnose, malformed, leavings, claude_stand_in
):
    left, _ = malformed["a missing private analysis"]
    claude_stand_in({"leaves": left}, {"leaves": leavings(), "sleep": 30})

    ran = diagnose("--timeout", "1.5")

    assert ran.code == 1
    assert "the diagnostician's session ran out of time" in ran.err
    assert not (data_root.diagnoses / NAME / "session.json").exists()


def test_a_session_that_runs_out_of_turns_fails_the_diagnosis(
    data_root: DataRoot, diagnose, leavings, claude_stand_in
):
    stand_in = claude_stand_in(
        {
            "leaves": leavings(),
            "result": {"is_error": True, "subtype": "error_max_turns", "result": None},
        }
    )

    ran = diagnose()

    assert ran.code == 1
    assert f"the diagnostician's session failed, so the diagnosis {NAME} failed" in ran.err
    # It isn't resumed, and what it left is no diagnosis.
    assert len(stand_in.seen()) == 1
    diagnosis = data_root.diagnoses / NAME
    assert not (diagnosis / "session.json").exists()
    assert "error_max_turns" in (diagnosis / "session-error.txt").read_text()


def test_the_session_runs_as_the_configuration_says(
    diagnose, leavings, claude_stand_in, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_DIAGNOSTICIAN_MODEL", "claude-fable-5-2")
    monkeypatch.setenv("PA_DIAGNOSTICIAN_EFFORT", "xhigh")
    monkeypatch.setenv("PA_DIAGNOSTICIAN_MAX_TURNS", "320")
    stand_in = claude_stand_in({"leaves": leavings()})

    assert diagnose().code == 0

    (seen,) = stand_in.seen()
    argv = seen["argv"]
    assert flag(argv, "--model") == "claude-fable-5-2"
    assert flag(argv, "--effort") == "xhigh"
    assert flag(argv, "--max-turns") == "320"


def test_an_unconfigured_session_runs_on_the_defaults_and_a_resumed_one_the_same(
    diagnose, malformed, leavings, claude_stand_in
):
    left, _ = malformed["a missing private analysis"]
    stand_in = claude_stand_in({"leaves": left}, {"leaves": leavings()})

    assert diagnose().code == 0

    for seen in stand_in.seen():
        argv = seen["argv"]
        assert (flag(argv, "--model"), flag(argv, "--effort")) == ("claude-opus-5-5", "high")
        assert flag(argv, "--max-turns") == "200"


def test_the_configured_timeout_bounds_the_session(
    diagnose, leavings, claude_stand_in, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_DIAGNOSTICIAN_TIMEOUT_SECONDS", "0.5")
    claude_stand_in({"leaves": leavings(), "sleep": 30})

    ran = diagnose()

    assert ran.code == 1
    assert "the diagnostician's session ran out of time" in ran.err


def test_an_option_overrides_the_configuration(
    diagnose, leavings, claude_stand_in, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_DIAGNOSTICIAN_MODEL", "claude-fable-5-2")
    monkeypatch.setenv("PA_DIAGNOSTICIAN_MAX_TURNS", "320")
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = diagnose("--agent-model", "opus", "--effort", "low", "--max-turns", "40")

    assert ran.code == 0
    (seen,) = stand_in.seen()
    argv = seen["argv"]
    assert (flag(argv, "--model"), flag(argv, "--effort")) == ("opus", "low")
    assert flag(argv, "--max-turns") == "40"


def test_configuration_that_isnt_a_turn_limit_is_refused_before_a_session_starts(
    data_root: DataRoot, diagnose, leavings, claude_stand_in, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PA_DIAGNOSTICIAN_MAX_TURNS", "many")
    stand_in = claude_stand_in({"leaves": leavings()})

    ran = diagnose()

    assert ran.code == 1
    assert "PA_DIAGNOSTICIAN_MAX_TURNS must be a whole number" in ran.err
    assert stand_in.seen() == []
    assert not data_root.diagnoses.exists()
