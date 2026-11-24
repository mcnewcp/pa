"""`pa-eval release`: check a diagnosis, and release its findings or refuse it (see
`pa_evals.release_check`).

It runs on the devbox, never where the diagnostician's session ran. The diagnosis is the one
named, in the private diagnoses directory under the data root; a release goes to a directory of
the same name in the released directory (`PA_RELEASED_DIR`), outside the repository and the data
root, where a development session may read it.

The findings are checked against what the case holds and against the owner's identity
(`PA_OWNER_NAME`, `PA_OWNER_EMAILS`, `PA_OWNER_OTHER_NAMES`), so it refuses to run without one.

What it prints is the diagnosis's name, the released directory, and counts: the findings by
kind, the reproductions released with them and the payloads those hold, and each rule by name
with how many problems it found. Never a line of the findings or of a reproduction, or what a
rule found; that is in the check's notes, kept with the private analysis.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pa_core.errors import PaError
from pa_evals.diagnosis import Case, Diagnosis, require_name
from pa_evals.real_data import require_private
from pa_evals.release_check import Refusal, release
from pa_home.config import (
    RELEASED_DIR_VAR,
    DataRoot,
    load_data_root,
    load_owner,
    load_released_directory,
    require_outside_repository,
)
from pa_home.cut import load_cut

REFUSED = 3
"""The exit code of a refusal: a diagnosis that was checked and not released. A failure (a
diagnosis that couldn't be checked) exits 1, as every `pa-eval` error does."""


class ReleaseChoiceError(PaError):
    """Options or directories that don't make a release."""


def add_release_command(commands: Any) -> None:
    command = commands.add_parser(
        "release",
        help="Check a diagnosis, and release its findings and reproductions to the released "
        "directory or refuse it.",
    )
    command.add_argument("--name", required=True, help="The diagnosis's name.")
    command.add_argument(
        "--cut",
        type=Path,
        required=True,
        metavar="DIR",
        help="The case the diagnosis read: the cut (`pa cut`), in this machine's copy.",
    )
    command.set_defaults(handler=_release)


def _release(args: argparse.Namespace) -> int:
    name = require_name(args.name)
    # Every path is checked before any is read.
    data_root = load_data_root()
    owner = load_owner()
    released_root = load_released_directory(data_root)
    cut_dir = require_private(args.cut, "--cut")
    private = _private(data_root, name)
    released = _released(released_root, data_root, name)
    if not private.is_dir():
        raise ReleaseChoiceError(f"there is no diagnosis named {name} in {data_root.diagnoses}")
    verdict = release(
        Diagnosis(private), Case(cut=load_cut(cut_dir)), owner=owner, released=released
    )
    if isinstance(verdict, Refusal):
        print(f"release: {name}: refused, and nothing is released")
        _print_checks(verdict.checks)
        print(f"notes: {verdict.notes}")
        return REFUSED
    record = verdict.record
    kinds = ", ".join(f"{count} {kind}" for kind, count in record.kinds.items())
    print(f"release: {name}: released")
    print(f"released: {verdict.directory}")
    print(f"findings: {record.findings}" + (f" ({kinds})" if kinds else ""))
    print(f"reproductions: {record.reproductions}" + (", untested" if record.reproductions else ""))
    print(f"payloads: {record.payloads}")
    _print_checks(record.checks)
    return 0


def _print_checks(checks: dict[str, int]) -> None:
    for rule, problems in checks.items():
        print(f"check {rule}: {problems}")


def _private(data_root: DataRoot, name: str) -> Path:
    """The diagnosis's private directory: under the data root, after following links, and so
    never inside the repository."""
    path = require_private(data_root.diagnoses / name, "the private directory")
    if not path.is_relative_to(data_root.path.resolve()):
        raise ReleaseChoiceError(
            f"the private directory ({path}) isn't under the data root ({data_root.path}): a "
            "diagnosis's private analysis stays there"
        )
    return path


def _released(released_root: Path, data_root: DataRoot, name: str) -> Path:
    """The diagnosis's released directory: outside the repository and the data root, after
    following links."""
    path = released_root / name
    resolved = path.resolve()
    require_outside_repository(
        resolved,
        "the released directory",
        "Released findings name opaque ids, which stay out of the repository.",
    )
    if resolved.is_relative_to(data_root.path.resolve()):
        raise ReleaseChoiceError(
            f"the released directory ({resolved}) is inside the data root ({data_root.path}): "
            f"set {RELEASED_DIR_VAR} to a directory a development session may read"
        )
    return path
