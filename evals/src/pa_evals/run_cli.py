"""`pa-eval run`: ask the eval set against a throwaway PA built from the corpus, and score it.

Agent runners and judges are picked by name from `AGENTS` and `JUDGES`. A new one adds one
entry there: a `Choice` that builds it from the parsed arguments, adds the options it needs, and
says how it was set up for the top of the report.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pa_core.errors import PaError
from pa_core.model_client import ModelClient
from pa_evals import frozen
from pa_evals.agent import (
    AgentError,
    AgentRunner,
    ClaudeAgentRunner,
    ScriptedAgent,
    ScriptedAnswer,
)
from pa_evals.eval_set import load_eval_set
from pa_evals.harness import DEFAULT_CONCURRENCY, run_eval
from pa_evals.judge import Judge, ModelJudge, ScriptedJudge, Verdict
from pa_evals.report import format_score, write_run
from pa_home.agent import DEFAULT_AS_OF_METHOD, DEFAULT_TIMEOUT_SECONDS, AsOfMethod, ClaudeAgent
from pa_home.claude_model import ClaudeCliBackend

DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"
"""`evals/runs/` in the workspace, which git ignores."""


AS_OF_ADR = "docs/adr/0003-as-of-time-injection.md"
"""Where the as-of injection methods were compared, and why the default won."""

AS_OF_REASONS: dict[AsOfMethod, str] = {
    AsOfMethod.SYSTEM_PROMPT: "the default: it and a preamble both passed every as-of canary, "
    "and it leaves the question exactly as asked and works in an interactive session too",
    AsOfMethod.PREAMBLE: "the alternative: it passed every as-of canary too, but it adds to the "
    "question, and the agent credits the date to the owner",
    AsOfMethod.NONE: "the control: the agent is not told the as-of time, so it goes by the "
    "real date",
}
"""Each as-of method's one-line reason for the report, summing up AS_OF_ADR."""


class ScriptedInputError(PaError):
    """A scripted answers or verdicts file that is not given, missing, or malformed."""


@dataclass(frozen=True)
class Choice[T]:
    """An agent runner or a judge that `pa-eval run` can be given by name."""

    build: Callable[[argparse.Namespace], T]
    """Makes it from the parsed arguments."""
    add_options: Callable[[argparse.ArgumentParser], None] = lambda parser: None
    """Adds the options only it uses to the `run` parser."""
    setup: Callable[[argparse.Namespace], dict[str, str]] = lambda args: {}
    """How it was set up, shown at the top of the report after its name."""


def _scripted_agent(args: argparse.Namespace) -> AgentRunner:
    if args.answers is None:
        raise ScriptedInputError("--agent scripted needs --answers FILE")
    answers: dict[str, ScriptedAnswer] = {}
    for question_id, answer in _read_script(args.answers).items():
        if isinstance(answer, dict) and isinstance(answer.get("error"), str):
            answers[question_id] = AgentError(answer["error"])
        elif isinstance(answer, str):
            answers[question_id] = answer
        else:
            raise ScriptedInputError(
                f"{args.answers}: the answer to {question_id} must be text or "
                '{"error": "message"}'
            )
    return ScriptedAgent(answers)


def _scripted_agent_options(run: argparse.ArgumentParser) -> None:
    run.add_argument(
        "--answers",
        type=Path,
        help='For --agent scripted: JSON of question id to answer text or {"error": "..."}.',
    )


def _claude_agent(args: argparse.Namespace) -> AgentRunner:
    return ClaudeAgentRunner(
        ClaudeAgent(
            model=args.agent_model,
            timeout_seconds=args.agent_timeout,
            as_of_method=args.as_of_method,
        )
    )


def _claude_agent_options(run: argparse.ArgumentParser) -> None:
    run.add_argument(
        "--agent-model", help="For --agent claude: the claude model (default: the CLI's default)."
    )
    run.add_argument(
        "--as-of-method",
        type=AsOfMethod,
        choices=list(AsOfMethod),
        default=DEFAULT_AS_OF_METHOD,
        help="For --agent claude: how the agent is told the as-of time "
        f"(default: {DEFAULT_AS_OF_METHOD}; see docs/adr/0003). 'none' is the control.",
    )
    run.add_argument(
        "--agent-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="For --agent claude: seconds before a question fails as timed out "
        f"(default: {DEFAULT_TIMEOUT_SECONDS}).",
    )


def _claude_agent_setup(args: argparse.Namespace) -> dict[str, str]:
    return {
        "agent model": args.agent_model or "default",
        "as-of method": str(args.as_of_method),
        "why this as-of method": AS_OF_REASONS[args.as_of_method],
        "as-of methods compared in": AS_OF_ADR,
    }


def _scripted_judge(args: argparse.Namespace) -> Judge:
    if args.verdicts is None:
        raise ScriptedInputError("--judge scripted needs --verdicts FILE")
    try:
        verdicts = {
            question_id: Verdict.model_validate(verdict)
            for question_id, verdict in _read_script(args.verdicts).items()
        }
    except ValidationError as error:
        raise ScriptedInputError(f"{args.verdicts}: {error}") from error
    return ScriptedJudge(verdicts)


def _scripted_judge_options(run: argparse.ArgumentParser) -> None:
    run.add_argument(
        "--verdicts",
        type=Path,
        help="For --judge scripted: JSON of question id to verdict "
        "(correct, reasoning, relevant_citations).",
    )


def _model_judge(args: argparse.Namespace) -> Judge:
    return ModelJudge(ModelClient(ClaudeCliBackend(model=args.judge_model)))


def _model_judge_options(run: argparse.ArgumentParser) -> None:
    run.add_argument(
        "--judge-model", help="For --judge model: the claude model (default: the CLI's default)."
    )


def _model_judge_setup(args: argparse.Namespace) -> dict[str, str]:
    return {"judge model": args.judge_model or "default"}


AGENTS: dict[str, Choice[AgentRunner]] = {
    "scripted": Choice(_scripted_agent, _scripted_agent_options),
    "claude": Choice(_claude_agent, _claude_agent_options, _claude_agent_setup),
}
JUDGES: dict[str, Choice[Judge]] = {
    "scripted": Choice(_scripted_judge, _scripted_judge_options),
    "model": Choice(_model_judge, _model_judge_options, _model_judge_setup),
}


def add_run_command(commands: Any) -> None:
    run = commands.add_parser(
        "run",
        help="Build a throwaway PA from the corpus, ask the eval set, and write a scored report.",
    )
    run.add_argument(
        "--corpus",
        type=Path,
        default=frozen.PAYLOADS,
        help="Directory of raw payloads (default: the frozen corpus, evals/corpus/payloads/).",
    )
    run.add_argument(
        "--eval-set",
        type=Path,
        default=frozen.EVAL_SET,
        help="Eval set JSON file (default: the Argus eval set, evals/corpus/eval_set.json).",
    )
    run.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Where the timestamped run directory goes (default: evals/runs/, ignored by git).",
    )
    run.add_argument(
        "--concurrency",
        type=_at_least_one,
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
    run.add_argument("--judge", choices=sorted(JUDGES), required=True, help="Judge.")
    for choice in [*AGENTS.values(), *JUDGES.values()]:
        choice.add_options(run)
    run.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    eval_set = load_eval_set(args.eval_set)
    if args.only:
        eval_set = eval_set.only(*args.only)
    agent = AGENTS[args.agent].build(args)
    judge = JUDGES[args.judge].build(args)
    results = run_eval(
        args.corpus,
        eval_set,
        agent,
        judge,
        concurrency=args.concurrency,
        setup=_setup(args),
    )
    run_dir = write_run(results, args.runs_dir)
    overall = results.overall
    print(
        f"{overall.questions} questions, {overall.failed} failed; "
        f"correctness {format_score(overall.correctness)}, "
        f"evidence recall {format_score(overall.evidence_recall)}, "
        f"citation validity {format_score(overall.citation_validity)}"
    )
    print(f"report: {run_dir / 'report.md'}")
    print(f"results: {run_dir / 'results.json'}")
    return 0


def _setup(args: argparse.Namespace) -> dict[str, str]:
    """How the run was set up, as shown at the top of the report."""
    return {
        "agent": args.agent,
        **AGENTS[args.agent].setup(args),
        "judge": args.judge,
        **JUDGES[args.judge].setup(args),
    }


def _at_least_one(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _read_script(path: Path) -> dict[str, Any]:
    try:
        script = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ScriptedInputError(f"file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ScriptedInputError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(script, dict):
        raise ScriptedInputError(f"{path} must be a JSON object keyed by question id")
    return script
