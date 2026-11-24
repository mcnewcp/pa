"""`pa-eval diagnose`: have a diagnostician answer a referral over a case (see
`pa_evals.diagnosis`).

The case and the diagnosis left are real data: each is refused inside the repository. What it
prints says where the diagnosis was left and what the session cost, never what it wrote. The
findings it leaves are unchecked: `pa-eval release` checks and releases them, on the devbox.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.errors import PaError
from pa_evals.diagnosis import (
    DEFAULT_TIMEOUT_SECONDS,
    Case,
    ClaudeDiagnostician,
    Diagnostician,
    ScriptedDiagnostician,
    diagnose,
    require_name,
)
from pa_evals.real_data import require_private
from pa_evals.run_cli import workspace_commit
from pa_home.cut import load_cut


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
    command.add_argument(
        "--cut", type=Path, required=True, metavar="DIR", help="The case: a cut (`pa cut`)."
    )
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
        metavar="DIR",
        help="For --agent scripted: a directory holding the files the session leaves.",
    )
    command.add_argument(
        "--reply",
        type=Path,
        metavar="FILE",
        help="For --agent scripted: a file holding the session's final words.",
    )
    command.add_argument(
        "--agent-model", help="For --agent claude: the claude model (default: the CLI's default)."
    )
    command.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"For --agent claude: seconds before the session fails as timed out "
        f"(default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    command.set_defaults(handler=_diagnose)


def _diagnose(args: argparse.Namespace) -> int:
    name = require_name(args.name)
    # Every path is checked before any is read.
    cut_dir = require_private(args.cut, "--cut")
    out = require_private(args.out, "--out")
    case = Case(cut=load_cut(cut_dir))
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
    print(f"session: {_cost(session.cost_usd)}, {_turns(session.turns)}")
    print(f"left: {diagnosis.path}")
    return 0


def _diagnostician(args: argparse.Namespace) -> Diagnostician:
    if args.agent == "scripted":
        if args.leaves is None or not args.leaves.is_dir():
            raise DiagnoseChoiceError("--agent scripted needs --leaves DIR, a directory")
        reply = "Done." if args.reply is None else args.reply.read_text()
        return ScriptedDiagnostician(args.leaves, reply=reply)
    return ClaudeDiagnostician(model=args.agent_model, timeout_seconds=args.timeout)


def _cost(cost_usd: float | None) -> str:
    return "cost not reported" if cost_usd is None else f"${cost_usd:.2f}"


def _turns(turns: int | None) -> str:
    return "turns not reported" if turns is None else f"{turns} turns"
