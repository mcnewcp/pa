"""`pa-eval wiki-history`: a wiki's history exported to files, one for each run, so a
diagnostician's session can read what each run did without a git directory. The wikis here are
synthetic, and what their pages say never reaches what the command prints."""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path

import pytest

from pa_evals.cli import main
from pa_home.config import DataRoot
from pa_home.cut import Cut
from pa_home.git_wiki import GitWikiRepository

DREAMING_RUN = """\
dreaming run: 2 candidates, 1 applied, 0 proposed, 1 dismissed

Candidates:
- c1 · stale-loop · topics/marmalade-invoice · applied: topics/marmalade-invoice.md
- c2 · quiet-page · people/zebulon-quackenbush · dismissed

Librarian-Version: fedcba987654
Dreaming-Version: 0a1b2c3d4e5f
Dreaming-Applied: 1
Dreaming-Dismissed: 1
"""


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A dreamed copy of a wiki, as `pa librarian dream DIR --cut CUT` leaves one: its tree,
    and beside it the git directory holding the copy as it started and one dreaming run."""
    directory = tmp_path / "copies" / "dreamed"
    repository = GitWikiRepository.open(directory / "wiki", directory / "wiki.git")
    (directory / "wiki" / "topics").mkdir()
    (directory / "wiki" / "people").mkdir()
    (directory / "wiki" / "topics" / "marmalade-invoice.md").write_text(
        "# Marmalade invoice\n\n- Open: the wombat owes for two jars.\n"
    )
    (directory / "wiki" / "people" / "zebulon-quackenbush.md").write_text("# Zebulon\n")
    repository.commit("Start the copy to dream\n\nLibrarian-Version: fedcba987654\n")
    (directory / "wiki" / "topics" / "marmalade-invoice.md").write_text(
        "# Marmalade invoice\n\n- Closed: the wombat paid for both jars.\n"
    )
    (directory / "wiki" / "people" / "zebulon-quackenbush.md").unlink()
    repository.commit(DREAMING_RUN)
    return directory


def modes(top: Path) -> set[int]:
    return {stat.S_IMODE(path.stat().st_mode) for path in top.iterdir()}


def test_a_dreamed_copys_history_is_exported_as_a_file_for_each_run_oldest_first(
    tmp_path: Path, copy: Path, pa_eval
):
    out = tmp_path / "histories" / "dreamed"

    ran = pa_eval("wiki-history", "--copy", copy, "--out", out)

    assert ran.code == 0
    files = sorted(out.iterdir())
    assert [re.sub(r"[0-9a-f]{12}", "<commit>", file.name) for file in files] == [
        "0001-<commit>.patch",
        "0002-<commit>.patch",
        "0003-<commit>.patch",
    ]
    started, copied, dreamed = (file.read_text() for file in files)
    assert "Start the wiki" in started
    assert "Start the copy to dream" in copied
    # A run's file is named by its commit, and holds the commit's message whole.
    commit = re.search(r"^commit ([0-9a-f]{40})$", dreamed, re.MULTILINE)
    assert commit is not None and files[2].name == f"0003-{commit[1][:12]}.patch"
    assert DREAMING_RUN in dreamed
    # So a dreaming run's outcomes can be read from it, candidate by candidate.
    assert "- c1 · stale-loop · topics/marmalade-invoice · applied: " in dreamed
    assert "- c2 · quiet-page · people/zebulon-quackenbush · dismissed" in dreamed
    # And what the run changed, line by line: a page reworded, and a page gone.
    assert "-- Open: the wombat owes for two jars." in dreamed
    assert "+- Closed: the wombat paid for both jars." in dreamed
    assert "deleted file mode" in dreamed and "people/zebulon-quackenbush.md" in dreamed
    # The run before it holds the pages as the copy started with them.
    assert "+- Open: the wombat owes for two jars." in copied
    # It is the owner's alone, and the command prints counts and the path.
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert modes(out) == {0o600}
    assert ran.out.splitlines() == ["history: 3 runs", f"left: {out}"]


def test_the_history_of_a_cuts_own_wiki_stops_at_the_cuts_commit(
    tmp_path: Path, data_root: DataRoot, cut: Cut, pa_eval, monkeypatch: pytest.MonkeyPatch
):
    # The live wiki has moved on since the cut was taken.
    live = GitWikiRepository(data_root.wiki, data_root.wiki_git)
    (data_root.wiki / "index.md").write_text("# Index\n\nThe gooseberry run.\n")
    live.commit("Consolidate a batch\n\nLibrarian-Version: fedcba987654\n")
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    out = tmp_path / "histories" / "own"

    ran = pa_eval("wiki-history", "--cut", cut.path, "--out", out)

    assert ran.code == 0
    first, second = (file.read_text() for file in sorted(out.iterdir()))
    assert "Start the wiki" in first
    assert "Observe a batch" in second
    assert cut.manifest.wiki is not None
    assert f"commit {cut.manifest.wiki.commit}\n" in second
    assert "topics/daves-wedding.md" in second
    assert ran.out.splitlines() == ["history: 2 runs", f"left: {out}"]


INSIDE = Path(__file__).resolve().parents[1] / "runs" / "private-history-test-never-written"


@pytest.mark.parametrize("option", ["--copy", "--cut", "--out"])
def test_a_history_and_what_it_is_read_from_are_refused_inside_the_repository(
    tmp_path: Path, data_root: DataRoot, copy: Path, pa_eval, monkeypatch, option: str
):
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    out = tmp_path / "histories" / "dreamed"
    source = ("--copy", copy) if option == "--out" else (option, INSIDE)

    ran = pa_eval("wiki-history", *source, "--out", INSIDE if option == "--out" else out)

    assert ran.code == 1
    assert f"{option} ({INSIDE}) is inside the repository" in ran.err
    assert not INSIDE.exists()
    assert not out.exists()


def test_a_history_is_never_written_over(tmp_path: Path, copy: Path, pa_eval):
    out = tmp_path / "histories" / "dreamed"
    out.mkdir(parents=True)
    (out / "0001-0123456789ab.patch").write_text("an earlier export\n")

    ran = pa_eval("wiki-history", "--copy", copy, "--out", out)

    assert ran.code == 1
    assert "isn't empty" in ran.err
    assert [file.read_text() for file in out.iterdir()] == ["an earlier export\n"]


def test_a_copy_with_no_history_is_refused(tmp_path: Path, copy: Path, pa_eval):
    shutil.rmtree(copy / "wiki.git")
    out = tmp_path / "histories" / "dreamed"

    ran = pa_eval("wiki-history", "--copy", copy, "--out", out)

    assert ran.code == 1
    assert "has no wiki.git" in ran.err
    assert not out.exists()


def test_a_history_is_of_a_cuts_wiki_or_of_a_copy_and_never_both(tmp_path: Path, copy: Path):
    with pytest.raises(SystemExit):
        main(["wiki-history", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit):
        main(["wiki-history", "--copy", str(copy), "--cut", str(copy), "--out", str(tmp_path)])
