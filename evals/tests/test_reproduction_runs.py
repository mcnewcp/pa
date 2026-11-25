"""Running a released reproduction as a reproduction (#168): `pa-eval run --reproduction DIR`
asks its questions over its payloads, with its starting wiki or a dreamed copy of it, and
`pa-eval reproduction dream DIR COPY` dreams that copy.

The reproduction here is made up for these tests, about a choir's rehearsal, and released by
the module that owns the layout (`pa_evals.reproduction`), so no test spells a released
reproduction's files. Its sessions are the stand-in `claude` (the conftest at the repository
root), and its questions are answered and judged by script.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pa_core.librarian.replay import DreamingLogEntry, append_to_run_log
from pa_evals.agent import AgentAnswer, AgentRequest
from pa_evals.cli import main
from pa_evals.configurations import FILES_PLUS_WIKI
from pa_evals.eval_set import EvalSet, load_eval_set
from pa_evals.eval_wiki import dreamed_reproduction_wiki, reproduction_wiki
from pa_evals.harness import run_eval
from pa_evals.judge import ScriptedJudge
from pa_evals.reproduction import read_released, read_reproductions, try_out
from pa_evals.results import RunResults

OWNER = {
    "name": "Argus McNevans",
    "email_addresses": ["argus@example.com"],
    "other_names": ["Gus"],
}
"""The synthetic corpus's fictional owner, whose every reproduction is."""
AS_OF = "2026-10-14T20:00:00-05:00"
AS_OF_UTC = "2026-10-15T01:00:00+00:00"

REHEARSAL = """\
X-Gmail-Labels: Inbox
Message-ID: <rehearsal-1@mail.choir.example>
Date: Mon, 12 Oct 2026 21:30:00 -0500
From: Tamsin Okafor <tamsin@choir.example>
To: Argus McNevans <argus@example.com>
Subject: Rehearsal moves
Content-Type: text/plain; charset="utf-8"

The rehearsal moves to Thursday at seven, in the annex.
"""
FOLDERS = """\
X-Gmail-Labels: Sent
Message-ID: <folders-1@example.com>
Date: Tue, 13 Oct 2026 09:00:00 -0500
From: Argus McNevans <argus@example.com>
To: Tamsin Okafor <tamsin@choir.example>
Subject: Folders
Content-Type: text/plain; charset="utf-8"

I will bring the folders on Thursday.
"""
PERSON = "people/tamsin-okafor.md"
TOPIC = "topics/choir.md"
DAY = "days/2026-10-12.md"
LOOP = "- [ ] [[tamsin-okafor]] → me: the seating plan · asked 2026-08-20 {ep:gmail/rehearsal.eml}"
"""The choir's loop, open 55 days at the as-of time: more than lint's 30."""
WIKI = {
    PERSON: textwrap.dedent(
        """\
        ---
        type: person
        aliases: []
        status: active
        summary: Directs the choir.
        updated: 2026-10-13
        emails: [tamsin@choir.example]
        ---
        # Tamsin Okafor

        ## Current state
        Moved the rehearsal to Thursday {ep:gmail/rehearsal.eml}.

        ## Open loops

        ## Notes
        """
    ),
    TOPIC: textwrap.dedent(
        f"""\
        ---
        type: topic
        aliases: []
        status: active
        summary: The choir's autumn rehearsals.
        updated: 2026-10-13
        ---
        # Choir

        ## Current state
        The rehearsal is on Thursday at seven {{ep:gmail/rehearsal.eml}}.

        ## Decisions

        ## Open loops
        {LOOP}

        ## Notes
        """
    ),
    DAY: "- email about Rehearsal moves with [[tamsin-okafor]] and [[choir]] "
    "{ep:gmail/rehearsal.eml}\n",
    "days/2026-10-13.md": "- email about Folders with [[tamsin-okafor]] and [[choir]] "
    "{ep:gmail/folders.eml}\n",
}
"""A starting wiki with one thing for a dreaming run to judge: the choir's aging loop."""
QUESTION = "rehearsal-day"
QUESTIONS = {
    "as_of": AS_OF,
    "owner": OWNER,
    "time_zone": "America/Chicago",
    "questions": [
        {
            "id": QUESTION,
            "category": "single_fact_recall",
            "question": "Which day is the choir's rehearsal?",
            "expected_answer": "Thursday.",
            "evidence": ["gmail/rehearsal.eml"],
        }
    ],
}


@pytest.fixture
def released(tmp_path: Path) -> Callable[..., Path]:
    """Makes a released reproduction for the finding `f1`: left as a session leaves one, with
    the starting wiki unless told otherwise, and written as a release writes it."""

    def release(*, wiki: bool = True) -> Path:
        left = tmp_path / "output" / "reproductions" / "f1"
        files = {
            "expectation.md": "The assistant should say Thursday; it says Tuesday.\n",
            "questions.json": json.dumps(QUESTIONS),
            "payloads/gmail/rehearsal.eml": REHEARSAL,
            "payloads/gmail/folders.eml": FOLDERS,
            **({f"wiki/{path}": text for path, text in WIKI.items()} if wiki else {}),
        }
        for path, text in files.items():
            (left / path).parent.mkdir(parents=True, exist_ok=True)
            (left / path).write_text(text)
        [reproduction] = read_reproductions(tmp_path / "output")
        trial = try_out(reproduction)
        assert trial.problems == ()
        directory = tmp_path / "released" / "reproductions" / "f1"
        trial.write(directory)
        return directory

    return release


def asked(reproduction: Path) -> EvalSet:
    """The reproduction's eval set, read where `pa-eval reproduction resolve` leaves it."""
    return load_eval_set(reproduction / "eval_set.json")


@pytest.fixture
def scripts(tmp_path: Path) -> Callable[[Path], list[str]]:
    """The scripted agent's and judge's options for a run over a reproduction: an answer that
    cites the question's evidence, judged correct."""

    def write(reproduction: Path) -> list[str]:
        [evidence] = asked(reproduction).questions[0].evidence
        answers, verdicts = tmp_path / "answers.json", tmp_path / "verdicts.json"
        answers.write_text(json.dumps({QUESTION: f"Thursday [ep:{evidence}]."}))
        verdict = {"correct": True, "reasoning": "Same day.", "relevant_citations": [evidence]}
        verdicts.write_text(json.dumps({QUESTION: verdict}))
        agent = ["--agent", "scripted", "--answers", str(answers)]
        return [*agent, "--judge", "scripted", "--verdicts", str(verdicts)]

    return write


def run_over(reproduction: Path, runs: Path, scripts: list[str], *extra: object) -> int:
    args = ["run", "--reproduction", str(reproduction), "--runs-dir", str(runs)]
    return main([*args, *scripts, *(str(argument) for argument in extra)])


def results_of(runs: Path) -> RunResults:
    [run_dir] = runs.iterdir()
    return RunResults.model_validate_json((run_dir / "results.json").read_text())


def test_a_reproductions_questions_are_asked_over_its_payloads(tmp_path, released, scripts):
    reproduction = released()
    runs = tmp_path / "runs"

    assert run_over(reproduction, runs, scripts(reproduction)) == 0

    results = results_of(runs)
    assert results.ingest is not None
    assert (results.ingest.ingested, results.ingest.quarantined) == (2, 0)
    assert results.as_of.isoformat() == AS_OF
    assert [question.id for question in results.questions] == [QUESTION]
    assert results.overall.correctness == 1.0
    assert results.overall.evidence_recall == 1.0
    # Files alone: the starting wiki isn't given, though the reproduction has one.
    assert results.configuration == "files-alone"
    assert results.wiki is None
    # The report names the reproduction by its finding, where it would name a corpus or a cut.
    assert results.setup["reproduction"] == "f1"
    assert "corpus" not in results.setup and "cut" not in results.setup
    [run_dir] = runs.iterdir()
    assert "- Reproduction: f1\n" in (run_dir / "report.md").read_text()


WITH_WIKI = ("--configuration", "files-plus-wiki")
NO_VERSION = "none (the diagnostician wrote a reproduction's starting wiki)"
"""What a run says built a reproduction's starting wiki: no librarian version did."""


def files_under(path: Path) -> dict[str, bytes]:
    return {str(f.relative_to(path)): f.read_bytes() for f in path.rglob("*") if f.is_file()}


def test_files_plus_wiki_over_a_reproduction_asks_with_its_starting_wiki(
    tmp_path, released, scripts
):
    reproduction = released()
    runs = tmp_path / "runs"

    assert run_over(reproduction, runs, scripts(reproduction), *WITH_WIKI) == 0

    results = results_of(runs)
    assert results.configuration == "files-plus-wiki"
    assert results.overall.correctness == 1.0
    # No run log is needed, and the results name no librarian version: none built the wiki.
    assert not (reproduction / "run-log.jsonl").exists()
    assert results.wiki is not None
    assert results.wiki.librarian_version == NO_VERSION
    assert results.wiki.dreaming_version is None
    assert results.injection_checks is not None
    [run_dir] = runs.iterdir()
    assert f"- Wiki: librarian version {NO_VERSION}\n" in (run_dir / "report.md").read_text()


def test_the_agent_is_given_the_reproductions_starting_wiki_as_it_was_released(released):
    reproduction = released()
    wikis: list[dict[str, bytes]] = []

    class LookingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            assert request.wiki is not None
            wikis.append(files_under(request.wiki))
            return AgentAnswer("Thursday.")

    parts = read_released(reproduction)
    run_eval(
        parts.payloads,
        asked(reproduction),
        LookingAgent(),
        ScriptedJudge({}),
        configuration=FILES_PLUS_WIKI,
        wiki=reproduction_wiki(parts),
    )

    assert wikis == [files_under(reproduction / "wiki")]
    assert sorted(wikis[0]) == sorted([*WIKI, "index.md"])


def test_a_reproduction_without_a_starting_wiki_isnt_asked_with_one(
    tmp_path, released, scripts, capsys
):
    reproduction = released(wiki=False)
    runs = tmp_path / "runs"

    assert run_over(reproduction, runs, scripts(reproduction), *WITH_WIKI) == 1

    assert "the reproduction f1 holds no starting wiki" in capsys.readouterr().err
    assert not runs.exists()
    # Files alone asks it as it is.
    assert run_over(reproduction, runs, scripts(reproduction)) == 0


FIXTURES = Path(__file__).parent / "fixtures"
ANOTHER = {
    "--corpus": FIXTURES / "corpus",
    "--cut": FIXTURES / "no-such-cut",
    "--eval-set": FIXTURES / "eval_set.json",
}
"""What a run over a reproduction is never given: it asks its own questions of its own
payloads."""


@pytest.mark.parametrize("option", ANOTHER)
def test_a_reproduction_is_asked_alone(tmp_path, released, scripts, capsys, option):
    reproduction = released()
    runs = tmp_path / "runs"

    assert run_over(reproduction, runs, scripts(reproduction), option, ANOTHER[option]) == 1

    assert f"--reproduction goes without {option}" in capsys.readouterr().err
    assert not runs.exists()


def test_a_directory_that_isnt_a_released_reproduction_is_refused(
    tmp_path, released, scripts, capsys
):
    reproduction = released()
    runs = tmp_path / "runs"

    assert run_over(reproduction / "payloads", runs, scripts(reproduction)) == 1

    assert "isn't a released reproduction" in capsys.readouterr().err
    assert not runs.exists()


def test_files_alone_over_a_reproduction_takes_no_wiki(tmp_path, released, scripts, capsys):
    reproduction = released()
    runs = tmp_path / "runs"

    assert run_over(reproduction, runs, scripts(reproduction), "--wiki", tmp_path / "copy") == 1

    assert "files-alone gives the assistant no wiki: drop --wiki" in capsys.readouterr().err
    assert not runs.exists()


# Dreaming a reproduction's starting wiki: `pa-eval reproduction dream DIR COPY`.


@pytest.fixture
def librarian(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stand_in_claude: Path) -> Path:
    """The librarian as the environment configures it: the stand-in `claude` for its sessions,
    a time zone, and an instance owner who isn't the reproduction's. Returns the directory the
    stand-in keeps its script and its log in."""
    monkeypatch.setenv("PA_TIME_ZONE", "America/Chicago")
    monkeypatch.setenv("PA_LIBRARIAN_COMMAND", str(stand_in_claude))
    monkeypatch.setenv("PA_OWNER_NAME", "Riley Real")
    monkeypatch.setenv("PA_OWNER_EMAILS", "riley@example.org")
    monkeypatch.delenv("PA_OWNER_OTHER_NAMES", raising=False)
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
    return stand_in_claude.parent


def script(librarian: Path, *dreaming: dict) -> None:
    """What the next dreaming session does."""
    (librarian / "script.json").write_text(json.dumps({"dreaming": list(dreaming)}))


def sessions(librarian: Path) -> list[dict]:
    """What each session was given, in order."""
    log = librarian / "sessions.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def told_now(session: dict) -> str:
    """The time the session was told is now, as it was written."""
    argv = session["argv"]
    system_prompt = argv[argv.index("--append-system-prompt") + 1]
    stamp = re.search(r"; (\S+)\)\. Treat it as now\.", system_prompt)
    assert stamp is not None, system_prompt
    return stamp[1]


def head_of(copy: Path) -> str:
    """The last commit of the wiki in `copy`."""
    return subprocess.run(
        ["git", f"--git-dir={copy / 'wiki.git'}", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def run_log(copy: Path) -> list[dict]:
    return [json.loads(line) for line in (copy / "run-log.jsonl").read_text().splitlines()]


def dream(reproduction: Path, copy: Path) -> int:
    return main(["reproduction", "dream", str(reproduction), str(copy)])


def closed(reproduction: Path) -> str:
    """The choir's page as the reproduction holds it, with its loop closed."""
    page = (reproduction / "wiki" / TOPIC).read_text()
    [loop] = [line for line in page.splitlines() if line.startswith("- [ ]")]
    [evidence] = asked(reproduction).questions[0].evidence
    done = loop.replace("- [ ]", "- [x]") + f" · closed 2026-10-13 [ep:{evidence}]"
    return page.replace(loop, done)


APPLIED = {"candidate": "c01", "outcome": "applied", "pages": [TOPIC]}
DISMISSED = {"candidate": "c01", "outcome": "dismissed"}


def test_dreaming_a_reproduction_copies_its_starting_wiki_and_dreams_the_copy(
    tmp_path, librarian, released, capsys
):
    reproduction = released()
    as_released = files_under(reproduction)
    copy = tmp_path / "replays" / "f1-dreamed"
    script(librarian, {"write": TOPIC, "text": closed(reproduction)}, {"outcomes": [APPLIED]})

    assert dream(reproduction, copy) == 0

    # It prints what `pa librarian dream` prints for a copy: counts alone.
    head = head_of(copy)
    assert capsys.readouterr().out == (
        "reproduction dream: judged 1 candidates (1 applied, 0 proposed, 0 dismissed); "
        f"committed {head[:8]}; 0 lint warnings\n"
    )
    # The session was told the eval set's as-of time, and judged the wiki as it stood then.
    [session] = sessions(librarian)
    assert told_now(session) == AS_OF
    assert [candidate["kind"] for candidate in session["candidates"]] == ["loop-aging"]
    # It ran as the eval set's owner, whoever the environment names.
    assert "Argus McNevans" in session["claude_md"]
    assert "Riley Real" not in session["claude_md"]
    # The copy holds the wiki as dreamed, and its run log the dreaming run alone: as of the
    # eval set's as-of time, of a wiki no librarian version built.
    assert (copy / "wiki" / TOPIC).read_text() == closed(reproduction)
    [entry] = run_log(copy)
    assert (entry["run"], entry["status"], entry["commit"]) == ("dreaming", "committed", head)
    assert (entry["as_of"], entry["librarian_version"]) == (AS_OF_UTC, None)
    assert len(entry["dreaming_version"]) == 12
    # The reproduction is as it was released.
    assert files_under(reproduction) == as_released


def test_a_second_dreaming_run_dreams_the_copy_as_the_first_left_it(
    tmp_path, librarian, released, capsys
):
    reproduction = released()
    copy = tmp_path / "replays" / "f1-dreamed"
    # The first run closes the loop, and leaves another one behind, as old.
    [evidence] = asked(reproduction).questions[0].evidence
    programme = f"- [ ] me → [[tamsin-okafor]]: the programme · promised 2026-08-25 [ep:{evidence}]"
    left = closed(reproduction).replace("\n## Notes", f"{programme}\n\n## Notes")
    script(librarian, {"write": TOPIC, "text": left}, {"outcomes": [APPLIED]})
    assert dream(reproduction, copy) == 0
    first = head_of(copy)
    script(librarian, {"outcomes": [DISMISSED]})
    capsys.readouterr()

    assert dream(reproduction, copy) == 0

    # The second run judged the copy as the first left it, not the starting wiki again, at
    # the same as-of time.
    _, second = sessions(librarian)
    assert [(c["kind"], c["line"]) for c in second["candidates"]] == [("loop-aging", 17)]
    assert told_now(second) == AS_OF
    assert (copy / "wiki" / TOPIC).read_text() == left
    assert "judged 1 candidates (0 applied, 0 proposed, 1 dismissed)" in capsys.readouterr().out
    # Its commit follows the first's, and the run log holds both.
    assert [(entry["run"], entry["commit"], entry["as_of"]) for entry in run_log(copy)] == [
        ("dreaming", first, AS_OF_UTC),
        ("dreaming", head_of(copy), AS_OF_UTC),
    ]


def test_a_reproduction_without_a_starting_wiki_has_nothing_to_dream(
    tmp_path, librarian, released, capsys
):
    reproduction = released(wiki=False)
    copy = tmp_path / "replays" / "f1-dreamed"

    assert dream(reproduction, copy) == 1

    assert "the reproduction f1 holds no starting wiki" in capsys.readouterr().err
    assert not copy.exists()
    assert sessions(librarian) == []


def test_a_directory_that_holds_a_run_log_and_no_wiki_isnt_started_as_a_copy(
    tmp_path, librarian, released, capsys
):
    reproduction = released()
    copy = tmp_path / "replays" / "f1-dreamed"
    script(librarian, {"outcomes": [DISMISSED]})
    assert dream(reproduction, copy) == 0
    shutil.rmtree(copy / "wiki")
    before = files_under(copy)
    capsys.readouterr()

    # Starting it again would put the starting wiki under a run log that says it was dreamed.
    assert dream(reproduction, copy) == 1

    assert "isn't a copy this command can start" in capsys.readouterr().err
    assert files_under(copy) == before
    assert len(sessions(librarian)) == 1


# Asking with a dreamed copy: `pa-eval run --reproduction DIR --wiki COPY`.


def test_a_run_over_a_reproduction_asks_with_a_dreamed_copy_of_its_wiki_when_given_one(
    tmp_path, librarian, released, scripts
):
    reproduction = released()
    copy = tmp_path / "replays" / "f1-dreamed"
    script(librarian, {"write": TOPIC, "text": closed(reproduction)}, {"outcomes": [APPLIED]})
    assert dream(reproduction, copy) == 0
    [dreamed] = run_log(copy)
    runs = tmp_path / "runs"

    asking = run_over(reproduction, runs, scripts(reproduction), *WITH_WIKI, "--wiki", copy)

    assert asking == 0
    # The results name the version that dreamed the copy, and still no version that built it.
    results = results_of(runs)
    assert results.overall.correctness == 1.0
    assert results.wiki is not None
    assert results.wiki.librarian_version == NO_VERSION
    assert results.wiki.dreaming_version == dreamed["dreaming_version"]
    assert results.setup["reproduction"] == "f1"
    [run_dir] = runs.iterdir()
    assert (
        f"- Wiki: librarian version {NO_VERSION}, dreamed by librarian version "
        f"{dreamed['dreaming_version']}\n"
    ) in (run_dir / "report.md").read_text()


def test_the_agent_is_given_the_dreamed_copy_in_place_of_the_starting_wiki(
    tmp_path, librarian, released
):
    reproduction = released()
    copy = tmp_path / "replays" / "f1-dreamed"
    script(librarian, {"write": TOPIC, "text": closed(reproduction)}, {"outcomes": [APPLIED]})
    assert dream(reproduction, copy) == 0
    pages: list[str] = []

    class LookingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            assert request.wiki is not None
            pages.append((request.wiki / TOPIC).read_text())
            return AgentAnswer("Thursday.")

    parts, eval_set = read_released(reproduction), asked(reproduction)
    run_eval(
        parts.payloads,
        eval_set,
        LookingAgent(),
        ScriptedJudge({}),
        configuration=FILES_PLUS_WIKI,
        wiki=dreamed_reproduction_wiki(parts, copy, as_of=eval_set.as_of),
    )

    assert pages == [closed(reproduction)]
    assert pages != [(reproduction / "wiki" / TOPIC).read_text()]


DREAMING_VERSION = "ba9876543210"


def dreaming_run(**changes) -> DreamingLogEntry:
    """A run log entry as `pa-eval reproduction dream` appends one for a committed run."""
    run = DreamingLogEntry(
        run="dreaming",
        status="committed",
        commit="c3" * 20,
        as_of=datetime.fromisoformat(AS_OF),
        librarian_version=None,
        dreaming_version=DREAMING_VERSION,
        model="opus",
        effort="high",
        sessions=(),
        cost_usd=0.9,
        candidates=1,
        outcomes={"applied": 1, "proposed": 0, "dismissed": 0},
        lint_warnings=0,
        error=None,
    )
    return run.model_copy(update=changes)


def copy_with(reproduction: Path, directory: Path, *runs: DreamingLogEntry) -> Path:
    """A copy of the reproduction's starting wiki in `directory`, with `runs` in its run log."""
    shutil.copytree(reproduction / "wiki", directory / "wiki")
    for run in runs:
        append_to_run_log(directory / "run-log.jsonl", run)
    return directory


def _replayed(reproduction: Path, directory: Path) -> Path:
    return shutil.copytree(FIXTURES / "eval-wiki", directory)


def _never_dreamed(reproduction: Path, directory: Path) -> Path:
    return copy_with(reproduction, directory)


def _only_failed(reproduction: Path, directory: Path) -> Path:
    failed = dreaming_run(status="failed", commit=None, candidates=None, outcomes=None)
    return copy_with(reproduction, directory, failed)


def _as_of_another_time(reproduction: Path, directory: Path) -> Path:
    earlier = datetime.fromisoformat(AS_OF) - timedelta(days=7)
    return copy_with(reproduction, directory, dreaming_run(as_of=earlier))


def _of_a_wiki_a_librarian_built(reproduction: Path, directory: Path) -> Path:
    return copy_with(reproduction, directory, dreaming_run(librarian_version="0123456789ab"))


def _without_a_tree(reproduction: Path, directory: Path) -> Path:
    append_to_run_log(directory / "run-log.jsonl", dreaming_run())
    return directory


NOT_DREAMED_COPIES = {
    "an eval wiki replayed from a corpus": (_replayed, "has a replay's runs"),
    "a copy that was never dreamed": (_never_dreamed, r"has no run-log.jsonl"),
    "a copy whose dreaming runs all failed": (_only_failed, "has no committed dreaming run"),
    "a copy dreamed as of another time": (
        _as_of_another_time,
        "has a dreaming run as of 2026-10-08T01:00:00+00:00, not the eval set's as-of time "
        "(2026-10-15T01:00:00+00:00)",
    ),
    "a copy of a wiki a librarian built": (
        _of_a_wiki_a_librarian_built,
        "has a dreaming run of a wiki built by librarian version 0123456789ab, and no "
        "librarian version built a reproduction's starting wiki",
    ),
    "a directory without a wiki": (_without_a_tree, "has no wiki/"),
}
"""What isn't a dreamed copy of a reproduction's starting wiki: how to make one, and why it's
refused."""


@pytest.mark.parametrize(
    ("make", "reason"), NOT_DREAMED_COPIES.values(), ids=list(NOT_DREAMED_COPIES)
)
def test_a_run_over_a_reproduction_refuses_a_wiki_that_isnt_a_dreamed_copy_of_its_own(
    tmp_path, released, scripts, capsys, make, reason
):
    reproduction = released()
    runs = tmp_path / "runs"
    copy = make(reproduction, tmp_path / "replays" / "f1-dreamed")

    asking = run_over(reproduction, runs, scripts(reproduction), *WITH_WIKI, "--wiki", copy)

    assert asking == 1
    error = capsys.readouterr().err
    assert reason in error
    assert "isn't a dreamed copy of the reproduction's starting wiki" in error
    assert not runs.exists()


def test_a_copy_with_a_committed_dreaming_run_as_of_the_eval_sets_time_is_asked_with(
    tmp_path, released, scripts
):
    reproduction = released()
    runs = tmp_path / "runs"
    failed = dreaming_run(status="failed", commit=None, candidates=None, outcomes=None)
    copy = copy_with(reproduction, tmp_path / "replays" / "f1-dreamed", failed, dreaming_run())

    asking = run_over(reproduction, runs, scripts(reproduction), *WITH_WIKI, "--wiki", copy)

    assert asking == 0
    wiki = results_of(runs).wiki
    assert wiki is not None and wiki.dreaming_version == DREAMING_VERSION


def test_a_reproduction_without_a_starting_wiki_has_no_dreamed_copy(
    tmp_path, released, scripts, capsys
):
    with_wiki = released()
    copy = copy_with(with_wiki, tmp_path / "replays" / "f1-dreamed", dreaming_run())
    shutil.rmtree(with_wiki / "wiki")
    runs = tmp_path / "runs"

    asking = run_over(with_wiki, runs, scripts(with_wiki), *WITH_WIKI, "--wiki", copy)

    assert asking == 1
    assert "the reproduction f1 holds no starting wiki" in capsys.readouterr().err
    assert not runs.exists()
