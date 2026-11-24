"""`pa-eval diagnose`: have a diagnostician answer a referral over a case (see
`pa_evals.diagnosis`).

The case and the diagnosis left are real data: each is refused inside the repository. What it
prints says where the diagnosis was left and what the session cost, never what it wrote. The
findings it leaves are unchecked: `pa-eval release` checks and releases them, on the devbox.

A revision (`--revise DIR --notes FILE`) corrects a diagnosis the release check refused: a
new session is given the refused output and the check's notes, over the same referral and
case, as drafting's second pass is given a draft and the owner's notes. The refused output and
the notes are real data too, refused inside the repository, and the notes are never printed.
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
    revise,
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
        "--revise",
        type=Path,
        metavar="DIR",
        help="A revision: revise the diagnosis NAME in --out, whose output the release check "
        "refused. DIR is that output, as the check read it; the refused pass is kept in the "
        "diagnosis's directory, and what the session leaves is its output from then on.",
    )
    command.add_argument(
        "--notes",
        type=Path,
        metavar="FILE",
        help="With --revise: the release check's notes on that output, which the session is "
        "given. They quote real content, and are never printed.",
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
        "--revised",
        type=Path,
        action="append",
        metavar="DIR",
        help="For --agent scripted: a directory holding the files the session leaves when it "
        "revises (--revise), in place of --leaves. Given more than once, the first is what it "
        "leaves in the diagnosis's first revision, and the next in its second.",
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
    if (args.revise is None) != (args.notes is None):
        raise DiagnoseChoiceError("a revision needs both --revise DIR and --notes FILE")
    # Every path is checked before any is read.
    out = require_private(args.out, "--out")
    refused = None if args.revise is None else require_private(args.revise, "--revise")
    notes = None if args.notes is None else require_private(args.notes, "--notes")
    case = named_case(args).load()
    if not args.referral.is_file():
        raise DiagnoseChoiceError(f"the referral {args.referral} isn't a file")
    if refused is not None and notes is not None:
        if not notes.is_file():
            raise DiagnoseChoiceError(f"the check's notes {notes} aren't a file")
        diagnosis = revise(
            args.referral.read_text(),
            case,
            _diagnostician(args),
            name=name,
            directory=out,
            refused=refused,
            notes=notes.read_text(encoding="utf-8", errors="replace"),
            commit=workspace_commit(),
        )
    else:
        diagnosis = diagnose(
            args.referral.read_text(),
            case,
            _diagnostician(args),
            name=name,
            directory=out,
            commit=workspace_commit(),
        )
    session = diagnosis.session()
    # The session's figures are every pass's together, a revision's with the passes before it.
    print(f"diagnosis: {diagnosis.name}, over the cut {case.cut.name}")
    print(f"case: {session.case.describe()}")
    print(f"session: {_cost(session.cost_usd)}, {_turns(session.turns)}")
    print(f"resumes: {session.resumes}")
    print(f"revisions: {session.revisions}")
    print(f"left: {diagnosis.path}")
    return 0


def _diagnostician(args: argparse.Namespace) -> Diagnostician:
    if args.agent == "scripted":
        scripted = [*(args.leaves or ()), *(args.revised or ())]
        if not args.leaves or not all(leaves.is_dir() for leaves in scripted):
            raise DiagnoseChoiceError(
                "--agent scripted needs --leaves DIR, a directory, and any --revised DIR is one too"
            )
        reply = "Done." if args.reply is None else args.reply.read_text()
        return ScriptedDiagnostician(*args.leaves, reply=reply, revised=args.revised or ())
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
