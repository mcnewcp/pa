"""The home wiki repository: git, with the git directory kept outside the wiki.

The wiki is the librarian's only writable mount, so nothing git reads may live in it: the git
directory sits beside the wiki in the data root (`wiki.git/`), never mounted into any VM, and
git is always told both directories rather than finding them. Git runs without the system's or
the user's configuration, so their settings (hooks, signing, filters) never apply to the wiki.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pa_core.errors import PaError

IDENTITY = ("-c", "user.name=PA librarian", "-c", "user.email=librarian@pa.invalid")
"""Who the wiki's commits are by: the host, on the librarian's behalf."""

FIRST_COMMIT = "Start the wiki"


class WikiRepositoryError(PaError):
    """The wiki's git repository could not be read or written."""


class GitWikiRepository:
    """The wiki at `tree`, with its history in the git directory `git_dir`."""

    def __init__(self, tree: Path, git_dir: Path) -> None:
        if git_dir.resolve().is_relative_to(tree.resolve()):
            raise WikiRepositoryError(
                f"The wiki's git directory ({git_dir}) is inside the wiki ({tree}); it must be "
                "outside it, where the librarian can't write."
            )
        self._tree = tree
        self._git_dir = git_dir

    @classmethod
    def open(cls, tree: Path, git_dir: Path) -> GitWikiRepository:
        """The repository, created with an empty first commit if it doesn't exist yet."""
        repository = cls(tree, git_dir)
        tree.mkdir(parents=True, exist_ok=True)
        if not git_dir.exists():
            repository._git("init", "--quiet", "--initial-branch=main")
            repository._git("commit", "--quiet", "--allow-empty", "--message", FIRST_COMMIT)
        return repository

    @property
    def tree(self) -> Path:
        return self._tree

    def last_commit_message(self) -> str | None:
        return self._git("log", "-1", "--format=%B")

    def commit(self, message: str) -> str:
        self._git("add", "--all")
        # A run whose session wrote nothing still moves the cursor.
        self._git("commit", "--quiet", "--allow-empty", "--file", "-", input=message)
        return self._git("rev-parse", "HEAD").strip()

    def discard(self) -> None:
        # Everything goes, whatever it is (an untracked file, a directory, a symbolic link, or
        # something posing as git's), and then the last commit is checked out again.
        for entry in self._tree.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        self._git("reset", "--quiet", "--hard", "HEAD")

    def _git(self, *args: str, input: str | None = None) -> str:
        command = [
            "git",
            f"--git-dir={self._git_dir}",
            f"--work-tree={self._tree}",
            *IDENTITY,
            *args,
        ]
        completed = subprocess.run(
            command, input=input, capture_output=True, text=True, env=git_environ(), check=False
        )
        if completed.returncode != 0:
            raise WikiRepositoryError(
                f"git {args[0]} failed on the wiki: {completed.stderr.strip() or 'no output'}"
            )
        return completed.stdout


def git_environ() -> dict[str, str]:
    """This process's environment without git's variables or configuration files, so git works
    on the repository it's pointed at, with nothing but that repository's own settings."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
