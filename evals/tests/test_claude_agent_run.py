"""`pa-eval run --agent claude`: the real agent runner, over a stand-in `claude` on PATH.

The stand-in answers with what it was told about "now", so the results show which time each
question was asked at, and prints stream-JSON events as the real CLI does: it greps L0, reads
one episode's file, and cites that episode and another it never opened. The real CLI is
exercised only by the opt-in smoke test at the end.
"""

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from pa_evals.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = FIXTURES / "corpus"
EVAL_SET = FIXTURES / "eval_set.json"

# Why the default won, as docs/adr/0003 records it.
WHY_SYSTEM_PROMPT = (
    "the default: it and a preamble both passed every as-of canary, and it leaves the question "
    "exactly as asked and works in an interactive session too"
)


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `claude` on PATH that answers with the system prompt it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json, sys
            argv = sys.argv[1:]
            sys.stdin.read()
            assert argv[argv.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in argv
            told = argv[argv.index("--append-system-prompt") + 1]
            model = argv[argv.index("--model") + 1] if "--model" in argv else "default"
            settings = json.load(open(".claude/settings.json"))
            directories = settings["permissions"]["additionalDirectories"]
            l0 = directories[0]
            wiki = directories[2] if len(directories) > 2 else None
            index = open(f"{wiki}/index.md").readline().strip().replace(" ", "_") if wiki else None
            opened = f"{l0}/episodes/gmail/2026-09/gmail_94f6b4cbc55a3b85.json"
            def tool(id, name, input, result, is_error=False):
                return [
                    {"type": "assistant", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": id, "name": name, "input": input}]}},
                    {"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": id, "content": result,
                         "is_error": is_error}]}},
                ]
            import os
            if os.environ.get("FAKE_CLAUDE_TOOLS"):
                with open(os.environ["FAKE_CLAUDE_TOOLS"]) as listed:
                    calls = [
                        json.loads(json.dumps(
                            (c["name"], c["input"], c.get("result", ""), c.get("is_error", False))
                        ).replace("{l0}", l0))
                        for c in json.load(listed)
                    ]
            else:
                calls = [
                    ("Bash", {"command": f"grep -rl hotel {l0}"}, "", True),
                    ("Read", {"file_path": opened}, "{}", False),
                ]
            events = [
                {"type": "system", "subtype": "init", "session_id": "s1"},
                *[
                    event
                    for number, (name, input, result, is_error) in enumerate(calls, start=1)
                    for event in tool(f"t{number}", name, input, result, is_error=is_error)
                ],
                {"type": "result", "subtype": "success", "is_error": False,
                 "result": f"[{model}] {told} [ep:gmail_94f6b4cbc55a3b85] "
                           f"[ep:gmail_424bdea9b0d82720] l0={l0} wiki={wiki} index={index}",
                 "total_cost_usd": 0.031, "num_turns": 5, "duration_ms": 12000},
            ]
            for event in events:
                print(json.dumps(event))
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return script


@pytest.fixture
def verdicts(tmp_path: Path) -> Path:
    path = tmp_path / "verdicts.json"
    verdict = {"correct": True, "reasoning": "Fine.", "relevant_citations": []}
    path.write_text(json.dumps({"day-of-week": verdict, "swim-instructor": verdict}))
    return path


def test_the_claude_runner_asks_each_question_at_its_as_of_time(
    tmp_path: Path, fake_claude: Path, verdicts: Path
):
    runs = tmp_path / "runs"

    exit_code = main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--only", "swim-instructor"),
            *("--agent", "claude", "--agent-model", "sonnet"),
            *("--judge", "scripted", "--verdicts", str(verdicts)),
        ]
    )

    assert exit_code == 0
    [run_dir] = runs.iterdir()
    results = json.loads((run_dir / "results.json").read_text())
    answers = {q["id"]: q["answer"] for q in results["questions"]}
    assert answers["day-of-week"].startswith("[sonnet] ")
    assert "Saturday, October 10, 2026, 09:00" in answers["day-of-week"]
    assert "Tuesday, October 6, 2026, 20:00" in answers["swim-instructor"]
    figures = {(q["cost_usd"], q["turns"], q["duration_ms"]) for q in results["questions"]}
    assert figures == {(0.031, 5, 12000)}
    [_, read] = results["questions"][0]["tool_calls"]
    assert read["tool"] == "Read" and read["errored"] is False
    assert read["input"]["file_path"].endswith("/gmail_94f6b4cbc55a3b85.json")
    assert [q["tool_calls"][0]["errored"] for q in results["questions"]] == [True, True]
    assert {tuple(q["cited_opened"]) for q in results["questions"]} == {("gmail_94f6b4cbc55a3b85",)}
    assert results["overall"]["cited_opened"] == 0.5
    assert {k: v for k, v in results["setup"].items() if k != "commit"} == {
        "corpus": str(CORPUS),
        "agent": "claude",
        "agent model": "sonnet",
        "as-of method": "system-prompt",
        "why this as-of method": WHY_SYSTEM_PROMPT,
        "as-of methods compared in": "docs/adr/0003-as-of-time-injection.md",
        "judge": "scripted",
    }
    report = (run_dir / "report.md").read_text()
    assert "- As-of method: system-prompt" in report
    assert f"- Why this as-of method: {WHY_SYSTEM_PROMPT}" in report
    assert "- As-of methods compared in: docs/adr/0003-as-of-time-injection.md" in report
    assert "## Cited episodes opened" in report
    assert "| canary | 0.50 |" in report
    assert "| Overall | 0.50 |" in report
    assert "- Tool calls: 2 (1 errored)" in report
    assert "- Cited but not opened: gmail_424bdea9b0d82720" in report


HOTEL = "gmail_94f6b4cbc55a3b85"
ENVELOPE = f"{{l0}}/episodes/gmail/2026-09/{HOTEL}.json"
RAW = f"{{l0}}/raw/gmail/2026-09/{HOTEL}.eml"
# A loop over files that grep found: the names are only ever in the shell variable.
LOOP = (
    'cd {l0}; for f in $(grep -rl hotel episodes); do echo "== $f"; python3 -c '
    "\"import json; d=json.load(open('$f')); print(d['subject']); print(d['body'])\"; done"
)
LOOP_OUTPUT = [
    {
        "type": "text",
        "text": f"== episodes/gmail/2026-09/{HOTEL}.json\n"
        "Re: Hotel block for the wedding\n"
        "Hi both,\n\nThe hotel block closes on Oct 9 — book soon!\n\nDave\n",
    }
]
SUBJECT = "Re: Hotel block for the wedding"
# The envelope's lines that match, its body still escaped as JSON.
GREPPED_ENVELOPE = (
    '  "subject": "Re: Hotel block for the wedding",\n'
    '  "body": "Hi both,\\n\\nThe hotel block closes on Oct 9 \\u2014 book soon!\\n\\nDave",\n'
)


def made(tool: str, input: dict[str, str], result: object = "", errored: bool = False) -> dict:
    """A tool call for the stand-in to make, with what the tool returned to it."""
    return {"name": tool, "input": input, "result": result, "is_error": errored}


@pytest.mark.parametrize(
    ("call", "is_opened"),
    [
        (made("Read", {"file_path": ENVELOPE}), True),
        (made("Read", {"file_path": RAW}), True),
        (made("Bash", {"command": LOOP}, LOOP_OUTPUT), True),
        (made("Bash", {"command": f"grep -i hotel {ENVELOPE}"}, GREPPED_ENVELOPE), True),
        (made("Grep", {"pattern": "hotel", "output_mode": "content"}, GREPPED_ENVELOPE), True),
        (made("Bash", {"command": "grep -rl hotel {l0}"}, f"{ENVELOPE}\n"), False),
        (made("Bash", {"command": f"ls -l {ENVELOPE}"}, f"-r--r--r-- 1 a a 912 {ENVELOPE}"), False),
        (made("Grep", {"pattern": "hotel", "path": "{l0}"}, f"Found 1 file\n{ENVELOPE}"), False),
        (made("Bash", {"command": f"jq -r .subject {ENVELOPE}"}, SUBJECT), False),
        (made("Bash", {"command": f"cat {ENVELOPE}"}, f"cat: {ENVELOPE}: denied", True), False),
        (made("Read", {"file_path": ENVELOPE}, "File does not exist.", True), False),
    ],
)
def test_a_cited_episode_counts_as_opened_when_its_contents_reached_the_session(
    tmp_path: Path,
    fake_claude: Path,
    verdicts: Path,
    monkeypatch: pytest.MonkeyPatch,
    call: dict,
    is_opened: bool,
):
    assert cited_opened(tmp_path, verdicts, monkeypatch, [call]) == ([HOTEL] if is_opened else [])


SHORT = "gmail_424bdea9b0d82720"
"""The other episode the stand-in cites, whose body is a single short line."""


@pytest.mark.parametrize(
    ("output", "is_opened"),
    [
        ("Hotel block for the wedding\nDave, is there a hotel block yet?\n", True),
        ("Dave, is there a hotel block yet?\n", False),
    ],
)
def test_a_short_body_counts_as_opened_only_beside_its_subject(
    tmp_path: Path,
    fake_claude: Path,
    verdicts: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    is_opened: bool,
):
    call = made("Bash", {"command": LOOP}, output)
    assert cited_opened(tmp_path, verdicts, monkeypatch, [call]) == ([SHORT] if is_opened else [])


def cited_opened(
    tmp_path: Path, verdicts: Path, monkeypatch: pytest.MonkeyPatch, calls: list[dict]
) -> list[str]:
    """The cited episodes opened, in a run where the stand-in makes `calls`."""
    listed = tmp_path / "tools.json"
    listed.write_text(json.dumps(calls))
    monkeypatch.setenv("FAKE_CLAUDE_TOOLS", str(listed))
    runs = tmp_path / "runs"

    main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--agent", "claude"),
            *("--judge", "scripted", "--verdicts", str(verdicts)),
        ]
    )

    [run_dir] = runs.iterdir()
    [question] = json.loads((run_dir / "results.json").read_text())["questions"]
    return question["cited_opened"]


def test_the_comparison_gives_each_configurations_share_of_cited_episodes_opened(
    tmp_path: Path, fake_claude: Path, verdicts: Path
):
    runs = tmp_path / "runs"

    main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--only", "swim-instructor", "--runs", "2"),
            *("--agent", "claude", "--judge", "scripted", "--verdicts", str(verdicts)),
        ]
    )

    [run_dir] = runs.iterdir()
    comparison = (run_dir / "comparison.md").read_text()
    assert "## Cited episodes opened" in comparison
    assert "| Category | files-alone |" in comparison
    # Every answer in every run cites two episodes and opens one.
    assert "| canary | 0.50 |" in comparison
    assert "| Overall | 0.50 |" in comparison


def test_files_plus_wiki_gives_the_claude_agent_the_eval_wiki_beside_the_throwaway_l0(
    tmp_path: Path, fake_claude: Path, verdicts: Path
):
    runs = tmp_path / "runs"
    eval_wiki = FIXTURES / "eval-wiki"

    exit_code = main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--agent", "claude"),
            *("--configuration", "files-plus-wiki", "--wiki", str(eval_wiki)),
            *("--judge", "scripted", "--verdicts", str(verdicts)),
        ]
    )

    assert exit_code == 0
    [run_dir] = runs.iterdir()
    [question] = json.loads((run_dir / "results.json").read_text())["questions"]
    told = dict(word.split("=", 1) for word in question["answer"].split() if "=" in word)
    assert Path(told["wiki"]) == Path(told["l0"]).parent / "wiki"
    assert told["index"] == "#_Index"  # the eval wiki's first line


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_the_day_of_week_canary_answers_with_the_as_of_date(tmp_path: Path):
    runs = tmp_path / "runs"
    model = os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku")

    main(
        [
            "run",
            *("--corpus", str(CORPUS), "--eval-set", str(EVAL_SET), "--runs-dir", str(runs)),
            *("--only", "day-of-week", "--agent", "claude", "--agent-model", model),
            *("--judge", "model", "--judge-model", model),
        ]
    )

    [run_dir] = runs.iterdir()
    [question] = json.loads((run_dir / "results.json").read_text())["questions"]
    assert question["status"] == "scored", question["error"]
    assert "Saturday" in question["answer"]
    assert question["correct"] is True, question["judge_reasoning"]
