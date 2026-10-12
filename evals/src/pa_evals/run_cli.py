"""`pa-eval run`: ask the eval set against a throwaway PA built from the corpus, and score it.

Agent runners and judges are picked by name from `AGENTS` and `JUDGES`. A new one adds one
entry there: a `Choice` that builds it from the parsed arguments, adds the options it needs, and
says how it was set up for the top of the report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from pa_core.errors import PaError
from pa_core.librarian.ports import ModelChoice
from pa_core.model_client import ModelClient
from pa_evals import frozen
from pa_evals.agent import (
    AgentAnswer,
    AgentError,
    AgentRunner,
    ClaudeAgentRunner,
    ScriptedAgent,
    ScriptedAnswer,
)
from pa_evals.comparison import COMPARISON, write_runs
from pa_evals.configurations import CONFIGURATIONS, DEFAULT_CONFIGURATION
from pa_evals.eval_set import load_eval_set
from pa_evals.harness import DEFAULT_CONCURRENCY, run_eval
from pa_evals.judge import Judge, ModelJudge, ScriptedJudge, Verdict
from pa_evals.librarian import EvalLibrarian
from pa_evals.report import format_cost, format_score
from pa_evals.results import RunResults
from pa_home.agent import DEFAULT_AS_OF_METHOD, DEFAULT_TIMEOUT_SECONDS, AsOfMethod, ClaudeAgent
from pa_home.claude_model import ClaudeCliBackend
from pa_home.config import load_librarian_models

WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_RUNS_DIR = WORKSPACE / "evals" / "runs"
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
    answers: dict[str, ScriptedAnswer | list[ScriptedAnswer]] = {
        question_id: (
            [_scripted_answer(args.answers, question_id, one) for one in answer]
            if isinstance(answer, list)
            else _scripted_answer(args.answers, question_id, answer)
        )
        for question_id, answer in _read_script(args.answers).items()
    }
    return ScriptedAgent(answers)


def _scripted_answer(path: Path, question_id: str, answer: Any) -> ScriptedAnswer:
    if isinstance(answer, str):
        return answer
    if isinstance(answer, dict) and isinstance(answer.get("error"), str):
        return AgentError(answer["error"])
    if isinstance(answer, dict) and isinstance(answer.get("answer"), str):
        try:
            scripted = _ScriptedFigures.model_validate(answer)
        except ValidationError as error:
            raise ScriptedInputError(f"{path}: the answer to {question_id}: {error}") from error
        return AgentAnswer(scripted.answer, scripted.cost_usd, scripted.turns, scripted.duration_ms)
    raise ScriptedInputError(
        f"{path}: the answer to {question_id} must be text, "
        '{"answer": "text", "cost_usd": 0.05, "turns": 4, "duration_ms": 9000} '
        '(the figures optional), or {"error": "message"}'
    )


class _ScriptedFigures(BaseModel, extra="forbid"):
    """A scripted answer with what it cost."""

    answer: str
    cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None


def _scripted_agent_options(run: argparse.ArgumentParser) -> None:
    run.add_argument(
        "--answers",
        type=Path,
        help="For --agent scripted: JSON of question id to answer text, "
        '{"answer": "...", "cost_usd": ..., "turns": ..., "duration_ms": ...}, '
        'or {"error": "..."}; or to a list of those, one per run.',
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
        verdicts: dict[str, Verdict | list[Verdict]] = {
            question_id: (
                [Verdict.model_validate(one) for one in verdict]
                if isinstance(verdict, list)
                else Verdict.model_validate(verdict)
            )
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


def default_librarian_cache(environ: Mapping[str, str] = os.environ) -> Path:
    """Where eval runs keep the librarian's cache: `$XDG_CACHE_HOME/pa-eval/librarian`, by
    default `~/.cache/pa-eval/librarian`. On the VM's disk when evals run in the VM."""
    cache_home = environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache_home) / "pa-eval" / "librarian"


def _librarian(args: argparse.Namespace) -> EvalLibrarian:
    """The librarian for a configuration with the graph: the instance's librarian models (as
    `pa librarian rebuild` takes them), on the `claude` CLI."""

    def backend_for(choice: ModelChoice) -> ClaudeCliBackend:
        return ClaudeCliBackend(model=choice.model, effort=choice.effort)

    return EvalLibrarian(
        args.librarian_cache.expanduser(),
        models=load_librarian_models(),
        backend_for=backend_for,
    )


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
        default=frozen.DEFAULT_CORPUS,
        help=f"A frozen corpus by name ({', '.join(frozen.CORPORA)}), asked with its own eval "
        "set, or a directory of raw payloads, asked with --eval-set "
        f"(default: {frozen.DEFAULT_CORPUS}).",
    )
    run.add_argument(
        "--eval-set",
        type=Path,
        help="Eval set JSON file (default: the named corpus's own; needed with --corpus DIR).",
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
    run.add_argument(
        "--configuration",
        choices=sorted(CONFIGURATIONS),
        default=DEFAULT_CONFIGURATION.name,
        help=f"What the agent is given (default: {DEFAULT_CONFIGURATION.name}). "
        + "; ".join(f"{c.name}: {c.about}" for c in CONFIGURATIONS.values())
        + ". One with the claims graph has the librarian build it from each run's L0, with the "
        "models `pa librarian rebuild` uses (PA_LIBRARIAN_MODEL and the rest).",
    )
    run.add_argument(
        "--librarian-cache",
        type=Path,
        default=default_librarian_cache(),
        help="Where the librarian's cache is kept between runs, so a later run over the same "
        "corpus extracts nothing again (default: %(default)s).",
    )
    run.add_argument(
        "--runs",
        type=_at_least_one,
        default=1,
        help="How many times to ask the whole eval set (default: 1). More than one writes each "
        "run and a comparison of them, with the spread between runs.",
    )
    run.add_argument("--agent", choices=sorted(AGENTS), required=True, help="Agent runner.")
    run.add_argument("--judge", choices=sorted(JUDGES), required=True, help="Judge.")
    for choice in [*AGENTS.values(), *JUDGES.values()]:
        choice.add_options(run)
    run.set_defaults(handler=_run)


def _run(args: argparse.Namespace) -> int:
    payloads, eval_set_path = _corpus(args.corpus, args.eval_set)
    eval_set = load_eval_set(eval_set_path)
    if args.only:
        eval_set = eval_set.only(*args.only)
    agent = AGENTS[args.agent].build(args)
    judge = JUDGES[args.judge].build(args)
    configuration = CONFIGURATIONS[args.configuration]
    librarian = _librarian(args) if configuration.graph else None
    setup = _setup(args)
    runs: list[RunResults] = []
    for number in range(1, args.runs + 1):
        results = run_eval(
            payloads,
            eval_set,
            agent,
            judge,
            concurrency=args.concurrency,
            configuration=configuration,
            librarian=librarian,
            setup=setup,
        )
        overall = results.overall
        label = f"run {number}: " if args.runs > 1 else ""
        print(
            f"{label}{overall.questions} questions, {overall.failed} failed; "
            f"correctness {format_score(overall.correctness)}, "
            f"evidence recall {format_score(overall.evidence_recall)}, "
            f"citation validity {format_score(overall.citation_validity)}, "
            f"total cost {format_cost(overall.total_cost_usd)}",
            flush=True,
        )
        runs.append(results)
    run_dir = write_runs(runs, args.runs_dir)
    if args.runs == 1:
        print(f"report: {run_dir / 'report.md'}")
        print(f"results: {run_dir / 'results.json'}")
    else:
        print(f"comparison: {run_dir / COMPARISON}")
    print(f"run directory: {run_dir}")
    return 0


def _corpus(corpus: str, eval_set: Path | None) -> tuple[Path, Path]:
    """The payload directory and eval set for `--corpus` (a name or a directory)."""
    if corpus in frozen.CORPORA:
        named = frozen.CORPORA[corpus]
        return named.payloads, eval_set or named.eval_set
    if not Path(corpus).is_dir():
        raise CorpusChoiceError(
            f"{corpus} is neither a corpus ({', '.join(frozen.CORPORA)}) nor a directory"
        )
    if eval_set is None:
        raise CorpusChoiceError("--corpus DIR needs --eval-set FILE")
    return Path(corpus), eval_set


class CorpusChoiceError(PaError):
    """A `--corpus` that names no corpus, or a corpus directory given without its eval set."""


def _setup(args: argparse.Namespace) -> dict[str, str]:
    """How the run was set up, as shown at the top of the report."""
    graph = {"librarian cache": str(args.librarian_cache)}
    return {
        "commit": _workspace_commit(),
        "corpus": args.corpus,
        **(graph if CONFIGURATIONS[args.configuration].graph else {}),
        "agent": args.agent,
        **AGENTS[args.agent].setup(args),
        "judge": args.judge,
        **JUDGES[args.judge].setup(args),
    }


def _workspace_commit() -> str:
    """The commit of the workspace the run's code comes from, so a report says what it ran.

    That commit also pins the agent project and the eval set. Changes to tracked files are
    flagged, since the commit alone doesn't describe them.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=WORKSPACE, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not run from a git checkout)"
    return f"{commit} (with uncommitted changes)" if changed else commit


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
