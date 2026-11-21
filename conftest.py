"""Stand-ins shared by every test root: the librarian's sessions' stand-in `claude`.

It lives here, above the four test roots (`pyproject.toml`'s `testpaths`), because the tests of
the home adapter, the eval harness, and the deploy scripts all run librarian sessions through
it, and test modules can't import each other (`--import-mode=importlib`).
"""

from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

import pytest


def write_stand_in_claude(directory: Path) -> Path:
    """The stand-in `claude` (adapters/home/tests/test_librarian_run.py says what it does), in
    `directory`, which also holds its log (`sessions.jsonl`) and its script (`script.json`)."""
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
def stand_in_claude(tmp_path: Path) -> Path:
    """The librarian's sessions' stand-in `claude`, logging and scripted in `tmp_path`."""
    return write_stand_in_claude(tmp_path)
