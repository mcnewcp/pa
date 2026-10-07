"""`pa-eval run`: ask the eval set against a throwaway PA built from the corpus, and score it.

Agent runners and judges are picked by name from `AGENTS` and `JUDGES`. A new one adds an entry
(a factory from the parsed arguments) and any options it needs in `add_run_command`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pa_core.errors import PaError
from pa_core.model_client import ModelClient
from pa_evals.agent import AgentError, AgentRunner, ScriptedAgent, ScriptedAnswer
from pa_evals.eval_set import load_eval_set
from pa_evals.harness import DEFAULT_CONCURRENCY, run_eval
from pa_evals.judge import Judge, ModelJudge, ScriptedJudge, Verdict
from pa_evals.report import write_run
from pa_home.claude_model import ClaudeCliBackend

DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"
"""`evals/runs/` in the workspace, which git ignores."""


class ScriptError(PaError):
    """A scripted answers or verdicts file that is missing or malformed."""


def _scripted_agent(args: argparse.Namespace) -> AgentRunner:
    if args.answers is None:
        raise ScriptError("--agent scripted needs --answers FILE")
    answers: dict[str, ScriptedAnswer] = {}
    for question_id, answer in _read_script(args.answers).items():
        if isinstance(answer, dict) and isinstance(answer.get("error"), str):
            answers[question_id] = AgentError(answer["error"])
        elif isinstance(answer, str):
            answers[question_id] = answer
        else:
            raise ScriptError(
                f"{args.answers}: the answer to {question_id} must be text or "
                '{"error": "message"}'
            )
    return ScriptedAgent(answers)


def _scripted_judge(args: argparse.Namespace) -> Judge:
    if args.verdicts is None:
        raise ScriptError("--judge scripted needs --verdicts FILE")
    try:
        verdicts = {
            question_id: Verdict.model_validate(verdict)
            for question_id, verdict in _read_script(args.verdicts).items()
        }
    except ValidationError as error:
        raise ScriptError(f"{args.verdicts}: {error}") from error
    return ScriptedJudge(verdicts)


def _model_judge(args: argparse.Namespace) -> Judge:
    return ModelJudge(ModelClient(ClaudeCliBackend(model=args.judge_model)))


AGENTS: dict[str, Callable[[argparse.Namespace], AgentRunner]] = {
    "scripted": _scripted_agent,
}
JUDGES: dict[str, Callable[[argparse.Namespace], Judge]] = {
    "scripted": _scripted_judge,
    "model": _model_judge,
}


def add_run_command(commands: Any) -> None:
    run = commands.add_parser(
        "run",
        help="Build a throwaway PA from the corpus, ask the eval set, and write a scored report.",
    )
    run.add_argument("--corpus", type=Path, required=True, help="Directory of raw payloads.")
    run.add_argument("--eval-set", type=Path, required=True, help="Eval set JSON file.")
    run.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Where the timestamped run directory goes (default: evals/runs/, ignored by git).",
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"How many questions run at once (default: {DEFAULT_CONCURRENCY}).",
    )
    run.add_argument(
        "--only",
        action="append",
        metavar="QUESTION_ID",
        help="Ask only this question; repeat for more.",
    )
    run.add_argument("--agent", choices=sorted(AGENTS), required=True, help="Agent runner.")
    run.add_argument(
        "--answers",
        type=Path,
        help='For --agent scripted: JSON of question id to answer text or {"error": "..."}.',
    )
    run.add_argument("--judge", choices=sorted(JUDGES), required=True, help="Judge.")
    run.add_argument(
        "--verdicts",
        type=Path,
        help="For --judge scripted: JSON of question id to verdict "
        "(correct, reasoning, relevant_citations).",
    )
    run.add_argument(
        "--judge-model", help="For --judge model: the claude model (default: the CLI's default)."
    )
    run.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    if args.concurrency < 1:
        raise ScriptError("--concurrency must be at least 1")
    eval_set = load_eval_set(args.eval_set)
    if args.only:
        eval_set = eval_set.only(*args.only)
    agent = AGENTS[args.agent](args)
    judge = JUDGES[args.judge](args)
    results = run_eval(
        args.corpus,
        eval_set,
        agent,
        judge,
        concurrency=args.concurrency,
        setup={"agent": args.agent, "judge": args.judge},
    )
    run_dir = write_run(results, args.runs_dir)
    overall = results.overall
    print(
        f"{overall.questions} questions, {overall.failed} failed; "
        f"correctness {_number(overall.correctness)}, "
        f"evidence recall {_number(overall.evidence_recall)}, "
        f"citation validity {_number(overall.citation_validity)}"
    )
    print(f"report: {run_dir / 'report.md'}")
    print(f"results: {run_dir / 'results.json'}")
    return 0


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _read_script(path: Path) -> dict[str, Any]:
    try:
        script = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ScriptError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ScriptError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(script, dict):
        raise ScriptError(f"{path} must be a JSON object keyed by question id")
    return script
