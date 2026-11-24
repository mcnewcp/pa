"""A wiki's history as files: what each run of the librarian did, for a session that has no
git directory to read.

A wiki's history is in its git directory, which is never laid in a case. So it is exported
here, on the devbox, to a directory a case can hold (`pa_evals.case`): one file for each
commit, oldest first, named `<number>-<commit>.patch`. Every commit of a wiki is a run of the
librarian (or the host starting the wiki, or reverting a run), so that is a file a run.

A file holds the commit's id and date, its message whole, and every change it made, as a diff.
A dreaming run's message lists each candidate it was offered with its outcome, so what a
dreaming run decided can be read from its file.

A history quotes the wiki's pages, so it is real content: it is written for the owner alone,
and nothing here puts a line of it, or of what git said, in an exception.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pa_core.errors import PaError
from pa_home.git_wiki import git_environ

_HEADER = "commit %H%ndate %cI%n%n%B"
"""What opens a run's file: the commit's id, when it was made, and its message."""
_COMMIT_CHARACTERS = 12
"""How much of a commit's id is in its file's name."""


class WikiHistoryError(PaError):
    """A history that can't be exported. Its message never quotes the wiki, or git."""


def export_history(git_dir: Path, out: Path, *, until: str | None = None) -> int:
    """Writes the history in the wiki's git directory `git_dir` to the directory `out`, a file
    for each run up to the commit `until` (default: the last), and returns how many.

    `out` must not exist yet, or be empty: a history is never written over. It is left
    readable by the owner alone.
    """
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise WikiHistoryError(f"{out} isn't empty: export a history into a new directory")
    if not git_dir.is_dir():
        raise WikiHistoryError(f"{git_dir} isn't a wiki's git directory")
    listed = _git(git_dir, "rev-list", "--reverse", until or "HEAD", "--").decode()
    commits = listed.split()
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    width = max(4, len(str(len(commits))))
    for number, commit in enumerate(commits, start=1):
        shown = _git(
            git_dir,
            "show",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            f"--format={_HEADER}",
            "--stat",
            "--patch",
            commit,
            "--",
        )
        path = out / f"{number:0{width}}-{commit[:_COMMIT_CHARACTERS]}.patch"
        path.touch(mode=0o600)
        path.write_bytes(shown)
    return len(commits)


def _git(git_dir: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        capture_output=True,
        env=git_environ(),
        check=False,
    )
    if completed.returncode != 0:
        # What git said may name a page, so it isn't repeated.
        raise WikiHistoryError(f"git {args[0]} failed on {git_dir}: its history can't be read")
    return completed.stdout
