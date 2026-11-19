"""Cuts: a frozen copy of the store, so a real-data eval set's answers never change as L0 grows.

A cut is taken now, and its as-of time is the moment it's taken. It lives under the data root,
in `cuts/<as-of, UTC, as 20261026T120000Z>/`, laid out as a data root is:

    l0/episodes/...       every episode captured by the as-of time, hard-linked from L0
    l0/raw/...            those episodes' raw payloads, hard-linked from L0
    wiki/                 the live wiki at its last commit, if it has one
    status/status.json    a copy of the status file, if there is one
    cut.json              the manifest (`CutManifest`)

L0's files are written once and never changed, so hard links share them safely; a file on
another filesystem is copied instead. A cut appears whole or not at all: it is built in a
hidden directory beside the others and renamed into place.
"""

from __future__ import annotations

import errno
import io
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from pa_core.errors import PaError
from pa_core.l0 import episode_ref
from pa_core.librarian.run import CURSOR_FIELD, VERSION_FIELD, commit_fields
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import git_environ

CUTS = "cuts"
"""The directory of cuts in a data root."""
MANIFEST = "cut.json"


class CutError(PaError):
    """A cut that can't be taken, or a directory that isn't a cut."""


class CutWiki(BaseModel):
    """The live wiki's commit a cut holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit: str
    librarian_version: str | None
    """The librarian version its last commit names; None for a commit that names none (a
    seed)."""
    cursor: str | None
    """Where the librarian had got to: the commit's cursor."""


class CutManifest(BaseModel):
    """What a cut holds, in its `cut.json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: AwareDatetime
    """When it was taken: it holds every episode captured by then."""
    episodes: int
    wiki: CutWiki | None
    """The wiki's commit, or None when the data root had no wiki."""
    status: bool
    """Whether it holds a status file."""


@dataclass(frozen=True)
class Cut:
    """A cut's directory: laid out as a data root is (`DataRoot`), with its manifest."""

    path: Path
    manifest: CutManifest

    @property
    def as_of(self) -> datetime:
        return self.manifest.as_of

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def l0(self) -> Path:
        return DataRoot(self.path).l0

    @property
    def wiki(self) -> Path | None:
        """The wiki's tree, or None when the cut holds no wiki."""
        return None if self.manifest.wiki is None else DataRoot(self.path).wiki

    @property
    def status(self) -> Path | None:
        """The status file, or None when the cut holds none."""
        return DataRoot(self.path).status if self.manifest.status else None

    def episode_ids(self) -> set[str]:
        """The ids of every episode in the cut."""
        return {path.stem for path in (self.l0 / "episodes").glob("*/*/*.json")}


def take_cut(data_root: DataRoot, *, now: datetime) -> Cut:
    """Cut the data root's store at `now`: every episode captured by then, the wiki at its last
    commit, and the status file. Hold the data root's lock around it, so no job writes the
    store while it's cut."""
    name = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    cuts = data_root.path / CUTS
    target = cuts / name
    if target.exists():
        raise CutError(f"a cut named {name} already exists")
    building = cuts / f".{name}.partial"
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    try:
        laid = DataRoot(building)
        episodes = _cut_l0(data_root.l0, laid.l0, now)
        wiki = _export_wiki(data_root, laid.wiki)
        status = data_root.status.is_file()
        if status:
            laid.status.parent.mkdir(parents=True)
            shutil.copy2(data_root.status, laid.status)
        manifest = CutManifest(as_of=now, episodes=episodes, wiki=wiki, status=status)
        (building / MANIFEST).write_text(manifest.model_dump_json(indent=2) + "\n")
        building.rename(target)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return Cut(target, manifest)


def load_cut(path: Path) -> Cut:
    """The cut in `path`; refuses a directory without a cut's manifest."""
    manifest = path / MANIFEST
    if not manifest.is_file():
        raise CutError(f"{path} isn't a cut: it has no {MANIFEST}")
    try:
        return Cut(path, CutManifest.model_validate_json(manifest.read_bytes()))
    except ValidationError as error:
        raise CutError(f"{manifest} isn't a cut's manifest: {error.errors()[0]['msg']}") from error


def _cut_l0(l0: Path, target: Path, as_of: datetime) -> int:
    """Link every episode captured by `as_of` (its envelope and raw payload) from `l0` into
    `target`; returns how many."""
    count = 0
    if not l0.is_dir():
        return count
    for envelope in FilesystemL0(l0).envelopes():
        if envelope.captured_at > as_of:
            continue
        for ref in (episode_ref(envelope), envelope.raw_ref):
            _link(l0 / ref, target / ref)
        count += 1
    return count


def _link(source: Path, target: Path) -> None:
    """Hard-link `source` at `target`, or copy it where a link can't be made."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError as error:
        if error.errno not in _CANT_LINK:
            raise
        shutil.copy2(source, target)


_CANT_LINK = {errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP}
"""A link across filesystems, or one the filesystem refuses."""


def _export_wiki(data_root: DataRoot, target: Path) -> CutWiki | None:
    """Write the wiki's tree at its last commit to `target`, never touching the live wiki or its
    repository; None when the data root has no wiki."""
    if not data_root.wiki_git.is_dir():
        return None
    git = _git(data_root.wiki_git)
    commit = git("rev-parse", "HEAD").decode().strip()
    fields = commit_fields(git("log", "-1", "--format=%B", commit).decode())
    archive = git("archive", "--format=tar", commit)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    return CutWiki(
        commit=commit,
        librarian_version=fields.get(VERSION_FIELD),
        cursor=fields.get(CURSOR_FIELD),
    )


def _git(git_dir: Path) -> Callable[..., bytes]:
    def git(*args: str) -> bytes:
        completed = subprocess.run(
            ["git", f"--git-dir={git_dir}", *args],
            capture_output=True,
            env=git_environ(),
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode(errors="replace").strip() or "no output"
            raise CutError(f"git {args[0]} failed on the wiki: {message}")
        return completed.stdout

    return git
