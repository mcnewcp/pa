"""`pa-eval diagnose`: have a diagnostician answer a referral over a case (see
`pa_evals.diagnosis`).

The case and the diagnosis left are real data: each is refused inside the repository. What it
prints says where the diagnosis was left and what the session cost, never what it wrote. The
findings it leaves are unchecked: `pa-eval release` checks and releases them, on the devbox.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from pa_core.errors import PaError
from pa_evals.case_cli import add_case_arguments, named_case
from pa_evals.diagnosis import (
    ClaudeDiagnostician,
    Diagnostician,
    ScriptedDiagnostician,
    diagnose,
    require_name,
)
from pa_evals.real_data import require_private
from pa_evals.run_cli import workspace_commit
from pa_home.config import (
    DIAGNOSTICIAN_EFFORT_VAR,
    DIAGNOSTICIAN_MAX_TURNS_VAR,
    DIAGNOSTICIAN_MODEL_VAR,
    DIAGNOSTICIAN_TIMEOUT_VAR,
    load_diagnostician_config,
)


class DiagnoseChoiceError(PaError):
    """Options that don't make a diagnosis."""


def add_diagnose_command(commands: Any) -> None:
    command = commands.add_parser(
        "diagnose",
        help="Have a diagnostician answer a referral over a case, and leave a private analysis "
        "and unchecked findings.",
    )
    command.add_argument(
        "--referral",
        type=Path,
        required=True,
        metavar="FILE",
        help="The referral: the question, in Markdown. It holds nothing real.",
    )
    add_case_arguments(command)
    command.add_argument(
        "--name",
        required=True,
        help="The diagnosis's name (letters, digits, and hyphens); never one already taken.",
    )
    command.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="DIR",
        help="The diagnoses directory: the diagnosis is left in DIR/NAME, for the owner alone.",
    )
    command.add_argument(
        "--agent", choices=["claude", "scripted"], required=True, help="Who diagnoses."
    )
    command.add_argument(
        "--leaves",
        type=Path,
        action="append",
        metavar="DIR",
        help="For --agent scripted: a directory holding the files the session leaves. Given "
        "more than once, the first is what it leaves when it is started, and each next one "
        "what it leaves when it is resumed.",
    )
    command.add_argument(
        "--reply",
        type=Path,
        metavar="FILE",
        help="For --agent scripted: a file holding the session's final words.",
    )
    command.add_argument(
        "--agent-model",
        help=f"For --agent claude: the claude model (default: {DIAGNOSTICIAN_MODEL_VAR}'s).",
    )
    command.add_argument(
        "--effort",
        help=f"For --agent claude: the session's effort (default: {DIAGNOSTICIAN_EFFORT_VAR}'s).",
    )
    command.add_argument(
        "--max-turns",
        type=_at_least_one,
        help="For --agent claude: the most turns the session may take, each time it is started "
        f"or resumed (default: {DIAGNOSTICIAN_MAX_TURNS_VAR}'s).",
    )
    command.add_argument(
        "--timeout",
        type=float,
        help="For --agent claude: seconds before the session fails as timed out, each time it "
        f"is started or resumed (default: {DIAGNOSTICIAN_TIMEOUT_VAR}'s).",
    )
    command.set_defaults(handler=_diagnose)


def _diagnose(args: argparse.Namespace) -> int:
    name = require_name(args.name)
    # Every path is checked before any is read.
    out = require_private(args.out, "--out")
    case = named_case(args).load()
    if not args.referral.is_file():
        raise DiagnoseChoiceError(f"the referral {args.referral} isn't a file")
    diagnosis = diagnose(
        args.referral.read_text(),
        case,
        _diagnostician(args),
        name=name,
        directory=out,
        commit=workspace_commit(),
    )
    session = diagnosis.session()
    print(f"diagnosis: {diagnosis.name}, over the cut {case.cut.name}")
    print(f"case: {session.case.describe()}")
    print(f"session: {_cost(session.cost_usd)}, {_turns(session.turns)}")
    print(f"resumes: {session.resumes}")
    print(f"left: {diagnosis.path}")
    return 0


def _diagnostician(args: argparse.Namespace) -> Diagnostician:
    if args.agent == "scripted":
        if not args.leaves or not all(leaves.is_dir() for leaves in args.leaves):
            raise DiagnoseChoiceError("--agent scripted needs --leaves DIR, a directory")
        reply = "Done." if args.reply is None else args.reply.read_text()
        return ScriptedDiagnostician(*args.leaves, reply=reply)
    config = load_diagnostician_config()
    asked = {
        "model": args.agent_model,
        "effort": args.effort,
        "max_turns": args.max_turns,
        "timeout_seconds": args.timeout,
    }
    return ClaudeDiagnostician(
        replace(config, **{name: value for name, value in asked.items() if value is not None})
    )


def _at_least_one(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("a whole number of at least 1")
    return int(value)


def _cost(cost_usd: float | None) -> str:
    return "cost not reported" if cost_usd is None else f"${cost_usd:.2f}"


def _turns(turns: int | None) -> str:
    return "turns not reported" if turns is None else f"{turns} turns"
