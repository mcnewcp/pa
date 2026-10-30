"""Cuts: a frozen copy of a data root's L0, wiki, and status, taken now, for real-data evals."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pa_core.catalog import open_catalog
from pa_core.envelope import Source
from pa_core.ingest import ingest
from pa_core.owner import Owner
from pa_core.status import SourceStatus, Status, write_status
from pa_home.cli import main
from pa_home.config import DataRoot
from pa_home.cut import CutError, load_cut, take_cut
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository
from pa_home.lock import data_root_lock

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",))
MONDAY = datetime(2026, 10, 5, 12, tzinfo=UTC)
TUESDAY = datetime(2026, 10, 6, 12, tzinfo=UTC)
WEDNESDAY = datetime(2026, 10, 7, 12, tzinfo=UTC)


def email(message_id: str, body: str) -> bytes:
    return (
        f"Message-ID: <{message_id}@example.net>\r\n"
        "Date: Mon, 05 Oct 2026 09:30:00 -0500\r\n"
        "From: David Kim <dkim@example.net>\r\n"
        "To: Argus McNevans <argus@example.com>\r\n"
        "Subject: Hotel block\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    ).encode()


def capture(data_root: DataRoot, at: datetime, **payloads: bytes) -> list[str]:
    """Ingest `payloads` (name to bytes, as gmail payloads) captured `at`; their episode ids."""
    for name, raw in payloads.items():
        path = data_root.inbox / "gmail" / f"{name}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    store = FilesystemL0(data_root.l0)
    before = {e.episode_id for e in store.envelopes()}
    with open_catalog(data_root.catalog, store) as catalog:
        ingest(data_root.inbox, store, catalog, OWNER, now=lambda: at)
    return sorted({e.episode_id for e in store.envelopes()} - before)


def files_under(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def data_root(tmp_path: Path) -> DataRoot:
    return DataRoot(tmp_path / "pa-data")


def test_a_cut_holds_exactly_the_episodes_captured_by_its_time(data_root: DataRoot):
    early = capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    late = capture(data_root, WEDNESDAY, b=email("b", "It closes Oct 12 now."))

    cut = take_cut(data_root, now=TUESDAY)

    assert cut.path.parent == data_root.path / "cuts"
    assert cut.as_of == TUESDAY
    assert cut.episode_ids() == set(early)
    assert set(early).isdisjoint(late)
    l0 = files_under(data_root.l0)
    assert files_under(cut.l0) == {
        name: raw for name, raw in l0.items() if any(i in name for i in early)
    }


def test_a_cut_hard_links_l0s_files_rather_than_copying_them(data_root: DataRoot):
    [episode] = capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))

    cut = take_cut(data_root, now=TUESDAY)

    [envelope] = (cut.l0 / "episodes").rglob(f"{episode}.json")
    original = data_root.l0 / envelope.relative_to(cut.l0)
    assert envelope.stat().st_ino == original.stat().st_ino


def commit_wiki(data_root: DataRoot, files: dict[str, str], message: str) -> str:
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    for name, text in files.items():
        path = data_root.wiki / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return repository.commit(message)


RUN_MESSAGE = (
    "Observe a batch\n\n"
    "Cursor: 2026-10-05T12:00:00+00:00 gmail_0123456789abcdef\n"
    "Librarian-Version: 0123456789ab\n"
)


def test_a_cut_holds_the_wiki_at_its_last_commit_and_names_that_commit(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    commit = commit_wiki(
        data_root, {"index.md": "# Index\n", "days/2026-10-05.md": "# Monday\n"}, RUN_MESSAGE
    )
    # A run in progress has changed the tree, and not committed it.
    (data_root.wiki / "days" / "2026-10-05.md").write_text("# Half written\n")
    (data_root.wiki / "days" / "2026-10-06.md").write_text("# Uncommitted\n")

    cut = take_cut(data_root, now=TUESDAY)

    assert cut.wiki is not None
    assert files_under(cut.wiki) == {"index.md": b"# Index\n", "days/2026-10-05.md": b"# Monday\n"}
    assert cut.manifest.wiki is not None
    assert cut.manifest.wiki.commit == commit
    assert cut.manifest.wiki.librarian_version == "0123456789ab"
    assert cut.manifest.wiki.cursor == "2026-10-05T12:00:00+00:00 gmail_0123456789abcdef"
    # The live wiki is left as it was.
    assert (data_root.wiki / "days" / "2026-10-06.md").read_text() == "# Uncommitted\n"


def test_a_cut_holds_a_copy_of_the_status_file(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    status = Status(
        sources={Source.GMAIL: SourceStatus(watermark=MONDAY, cadence=TUESDAY - MONDAY)}
    )
    write_status(data_root.status, status, at=MONDAY)

    cut = take_cut(data_root, now=TUESDAY)

    assert cut.status is not None
    assert cut.status.read_bytes() == data_root.status.read_bytes()
    assert cut.status.relative_to(cut.path) == data_root.status.relative_to(data_root.path)


def test_a_cut_of_a_data_root_without_a_wiki_or_status_holds_neither(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))

    cut = take_cut(data_root, now=TUESDAY)

    assert cut.wiki is None
    assert cut.status is None
    assert sorted(p.name for p in cut.path.iterdir()) == ["cut.json", "l0"]


def test_a_cut_loads_back_from_its_directory(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    commit_wiki(data_root, {"index.md": "# Index\n"}, RUN_MESSAGE)
    taken = take_cut(data_root, now=TUESDAY)

    loaded = load_cut(taken.path)

    assert loaded == taken
    assert loaded.manifest.episodes == 1
    assert loaded.name == "20261006T120000Z"


def test_a_directory_without_a_manifest_isnt_a_cut(tmp_path: Path):
    with pytest.raises(CutError, match="isn't a cut"):
        load_cut(tmp_path)


def test_two_cuts_at_the_same_moment_are_refused_and_the_first_is_kept(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    first = take_cut(data_root, now=TUESDAY)

    with pytest.raises(CutError, match="already exists"):
        take_cut(data_root, now=TUESDAY)

    assert load_cut(first.path) == first
    assert [p.name for p in (data_root.path / "cuts").iterdir()] == [first.name]


def test_pa_cut_cuts_the_configured_data_root_now_and_says_where(
    data_root: DataRoot, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    commit = commit_wiki(data_root, {"index.md": "# Index\n"}, RUN_MESSAGE)
    before = datetime.now(UTC)

    assert main(["cut"]) == 0

    [path] = (data_root.path / "cuts").iterdir()
    cut = load_cut(path)
    assert before <= cut.as_of <= datetime.now(UTC)
    assert capsys.readouterr().out.splitlines() == [
        f"cut: {path}",
        f"cut: 1 episodes, wiki at {commit[:12]}, no status file",
    ]


def test_pa_cut_refuses_while_another_run_holds_the_data_roots_lock(
    data_root: DataRoot, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("PA_DATA_DIR", str(data_root.path))
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))

    with data_root_lock(data_root):
        assert main(["cut"]) == 1

    assert "lock" in capsys.readouterr().err
    assert not (data_root.path / "cuts").exists()


def test_a_cut_that_fails_part_way_leaves_nothing_behind(data_root: DataRoot):
    capture(data_root, MONDAY, a=email("a", "The block closes Oct 9."))
    # Something that isn't a git directory where the wiki's should be.
    data_root.wiki_git.mkdir(parents=True)

    with pytest.raises(CutError, match="git"):
        take_cut(data_root, now=TUESDAY)

    assert list((data_root.path / "cuts").iterdir()) == []
