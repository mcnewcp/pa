"""Eval wikis: the wiki a files-plus-wiki run gives the assistant, built by a replay on the host.

An eval wiki is a directory holding a replay's final tree and its run log, as
`pa librarian replay TARGET` leaves them in TARGET:

    wiki/            the wiki's tree: index.md, days/, people/, orgs/, topics/
    run-log.jsonl    one entry per librarian run (`pa_core.librarian.replay` documents it)

The replay's git directory (`wiki.git/` beside them) is not part of it, and is never committed.
Eval wikis are built once per librarian version and kept in the evals tree by corpus and
librarian version (`frozen.eval_wiki`): `evals/wikis/<corpus>/<librarian version>/`. One is
committed when a reported result uses it, so a failure can be diagnosed from the repository.

The run log names the librarian version that built the wiki; the results record it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pa_core.errors import PaError

TREE = "wiki"
RUN_LOG = "run-log.jsonl"


class EvalWikiError(PaError):
    """A directory that isn't a finished eval wiki."""


@dataclass(frozen=True)
class EvalWiki:
    tree: Path
    """The wiki's tree, laid beside the throwaway L0 for the run."""
    librarian_version: str
    """The librarian version that built it, from its run log."""


def load_eval_wiki(directory: Path) -> EvalWiki:
    """The eval wiki in `directory`: its tree, and the librarian version its run log names.

    Refused unless the run log has a committed run, every run in it is of one librarian version,
    and its last run committed (a replay that stopped on a failed run isn't finished).
    """
    if not directory.is_dir():
        raise EvalWikiError(f"eval wiki not found: {directory}")
    tree, run_log = directory / TREE, directory / RUN_LOG
    if not tree.is_dir():
        raise EvalWikiError(f"{directory} has no {TREE}/: it isn't an eval wiki")
    if not run_log.is_file():
        raise EvalWikiError(f"{directory} has no {RUN_LOG}: it isn't an eval wiki")
    entries = _entries(run_log)
    if not any(entry.get("status") == "committed" for entry in entries):
        raise EvalWikiError(f"{run_log} has no committed run")
    versions = sorted({str(entry.get("librarian_version")) for entry in entries})
    if len(versions) > 1:
        raise EvalWikiError(f"{run_log} names more than one librarian version: {versions}")
    if entries[-1].get("status") != "committed":
        raise EvalWikiError(
            f"{run_log} ends with a failed run: finish the replay with --resume first"
        )
    return EvalWiki(tree, versions[0])


def _entries(run_log: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for number, line in enumerate(run_log.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise EvalWikiError(f"{run_log}:{number} is not JSON: {error}") from error
        if not isinstance(entry, dict):
            raise EvalWikiError(f"{run_log}:{number} is not a run log entry")
        entries.append(entry)
    return entries
