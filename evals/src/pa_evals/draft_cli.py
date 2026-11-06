"""`pa-eval draft`: have a session draft the real-data eval set from a cut, or revise a draft by
the owner's notes (see `pa_evals.drafting`).

The cut, the draft written, the draft revised, and the notes are all real data: each is refused
inside the repository. What it prints says how much was drafted, never what.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.atomic import write_atomically
from pa_core.errors import PaError
from pa_evals.drafting import (
    DEFAULT_QUESTIONS,
    ClaudeDrafter,
    Drafter,
    ScriptedDrafter,
    draft_eval_set,
    draft_json,
    evidence_count,
    revise_eval_set,
)
from pa_evals.eval_set import load_eval_set
from pa_evals.real_data import require_private
from pa_home.agent import ClaudeAgent
from pa_home.config import load_owner, load_time_zone
from pa_home.cut import load_cut

DEFAULT_TIMEOUT_SECONDS = 3600
"""How long a drafting session may take: it reads widely, so far longer than a question."""


class DraftChoiceError(PaError):
    """Options that don't make a drafting pass."""


def add_draft_command(commands: Any) -> None:
    draft = commands.add_parser(
        "draft",
        help="Have a session draft the real-data eval set from a cut, or revise a draft by the "
        "owner's notes.",
    )
    draft.add_argument(
        "--cut", type=Path, required=True, metavar="DIR", help="The cut (`pa cut`) to draft from."
    )
    draft.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="FILE",
        help="Where the draft is written; never over a file already there.",
    )
    draft.add_argument(
        "--revise",
        type=Path,
        metavar="DRAFT",
        help="A second pass: revise this draft (as the owner left it) by --notes.",
    )
    draft.add_argument(
        "--notes", type=Path, metavar="FILE", help="With --revise: the owner's notes on it."
    )
    draft.add_argument(
        "--questions",
        type=int,
        default=DEFAULT_QUESTIONS,
        help=f"About how many questions a first pass drafts (default: {DEFAULT_QUESTIONS}).",
    )
    draft.add_argument("--agent", choices=["claude", "scripted"], required=True, help="Who drafts.")
    draft.add_argument(
        "--reply", type=Path, help="For --agent scripted: a file holding the session's reply."
    )
    draft.add_argument(
        "--agent-model", help="For --agent claude: the claude model (default: the CLI's default)."
    )
    draft.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"For --agent claude: seconds before the session fails as timed out "
        f"(default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    draft.set_defaults(handler=_draft)


def _draft(args: argparse.Namespace) -> int:
    if (args.revise is None) != (args.notes is None):
        raise DraftChoiceError("a second pass needs both --revise DRAFT and --notes FILE")
    # Every path is checked before any is read.
    cut_dir = require_private(args.cut, "--cut")
    out = require_private(args.out, "--out")
    revise = None if args.revise is None else require_private(args.revise, "--revise")
    notes = None if args.notes is None else require_private(args.notes, "--notes")
    if out.exists():
        raise DraftChoiceError(f"{out} already exists: a draft is never written over")
    cut = load_cut(cut_dir)
    drafter = _drafter(args)
    if revise is not None and notes is not None:
        previous = load_eval_set(revise, cut=cut)
        eval_set = revise_eval_set(cut, drafter, previous=previous, notes=notes.read_text())
    else:
        eval_set = draft_eval_set(
            cut,
            drafter,
            owner=load_owner(),
            time_zone=load_time_zone().key,
            questions=args.questions,
        )
    write_atomically(out, draft_json(eval_set), mode=0o600)
    print(
        f"draft: {len(eval_set.questions)} questions over the cut {cut.name}, "
        f"naming {evidence_count(eval_set)} episodes"
    )
    print(f"draft: {out}")
    return 0


def _drafter(args: argparse.Namespace) -> Drafter:
    if args.agent == "scripted":
        if args.reply is None:
            raise DraftChoiceError("--agent scripted needs --reply FILE")
        return ScriptedDrafter(args.reply.read_text())
    return ClaudeDrafter(ClaudeAgent(model=args.agent_model, timeout_seconds=args.timeout))
