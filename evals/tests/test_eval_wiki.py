"""Eval wikis: a replay's final tree and run log, read for the librarian version that built it."""

import json
import shutil
from pathlib import Path

import pytest

from pa_evals import frozen
from pa_evals.eval_wiki import EvalWikiError, load_eval_wiki
from pa_evals.run_cli import WORKSPACE

FIXTURES = Path(__file__).parent / "fixtures"
EVAL_WIKI = FIXTURES / "eval-wiki"


def copy_of_the_fixture(tmp_path: Path) -> Path:
    copy = tmp_path / "eval-wiki"
    shutil.copytree(EVAL_WIKI, copy)
    return copy


def rewrite_run_log(directory: Path, change) -> None:
    run_log = directory / "run-log.jsonl"
    entries = [json.loads(line) for line in run_log.read_text().splitlines()]
    entries = change(entries)
    run_log.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def test_an_eval_wiki_is_its_tree_and_the_librarian_version_its_run_log_names():
    wiki = load_eval_wiki(EVAL_WIKI)

    assert wiki.tree == EVAL_WIKI / "wiki"
    assert wiki.librarian_version == "0123456789ab"
    assert wiki.dreaming_version is None


def test_a_directory_without_a_wiki_tree_is_refused(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    shutil.rmtree(directory / "wiki")

    with pytest.raises(EvalWikiError, match="no wiki/"):
        load_eval_wiki(directory)


def test_a_directory_without_a_run_log_is_refused(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    (directory / "run-log.jsonl").unlink()

    with pytest.raises(EvalWikiError, match=r"no run-log\.jsonl"):
        load_eval_wiki(directory)


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(EvalWikiError, match="not found"):
        load_eval_wiki(tmp_path / "nowhere")


def test_a_run_log_with_no_committed_run_is_refused(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    rewrite_run_log(directory, lambda entries: [e for e in entries if e["status"] == "failed"])

    with pytest.raises(EvalWikiError, match="no committed run"):
        load_eval_wiki(directory)


def test_a_replay_whose_last_run_failed_is_refused_as_unfinished(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    rewrite_run_log(directory, lambda entries: entries[:2])

    with pytest.raises(EvalWikiError, match="--resume"):
        load_eval_wiki(directory)


def test_a_run_log_naming_two_librarian_versions_is_refused(tmp_path):
    directory = copy_of_the_fixture(tmp_path)

    def two_versions(entries):
        entries[0]["librarian_version"] = "ba9876543210"
        return entries

    rewrite_run_log(directory, two_versions)

    with pytest.raises(EvalWikiError, match=r"0123456789ab.*ba9876543210"):
        load_eval_wiki(directory)


def test_a_malformed_run_log_is_refused(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    (directory / "run-log.jsonl").write_text("not json\n")

    with pytest.raises(EvalWikiError, match=r"run-log\.jsonl"):
        load_eval_wiki(directory)


def dreaming_run(**changes) -> dict:
    """A run log entry for a dreaming run of a copy of the fixture, by a later librarian."""
    run = {
        "run": "dreaming",
        "status": "committed",
        "commit": "c3" * 20,
        "as_of": "2026-10-07T01:00:00+00:00",
        "librarian_version": "0123456789ab",
        "dreaming_version": "ba9876543210",
        "model": "opus",
        "effort": "high",
        "sessions": [
            {"phase": "dreaming", "session_id": "s5", "cost_usd": 0.9, "turns": 20, "resumes": 0}
        ],
        "cost_usd": 0.9,
        "candidates": 4,
        "outcomes": {"applied": 2, "proposed": 1, "dismissed": 1},
        "lint_warnings": 0,
        "error": None,
    }
    return {**run, **changes}


FAILED = {"status": "failed", "commit": None, "candidates": None, "outcomes": None}


def test_a_dreamed_eval_wiki_names_the_version_that_built_it_and_the_one_that_dreamed_it(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    rewrite_run_log(
        directory, lambda entries: [*entries, dreaming_run(), dreaming_run(commit="d4" * 20)]
    )

    wiki = load_eval_wiki(directory)

    assert wiki.tree == directory / "wiki"
    assert (wiki.librarian_version, wiki.dreaming_version) == ("0123456789ab", "ba9876543210")


def test_a_dreaming_run_that_failed_leaves_an_eval_wiki_as_it_was(tmp_path):
    directory = copy_of_the_fixture(tmp_path)
    rewrite_run_log(directory, lambda entries: [*entries, dreaming_run(**FAILED)])

    assert load_eval_wiki(directory).dreaming_version is None

    rewrite_run_log(directory, lambda entries: [*entries[:-1], dreaming_run(), entries[-1]])

    assert load_eval_wiki(directory).dreaming_version == "ba9876543210"


MIXED = {
    "dreaming runs by two versions": (
        lambda entries: [*entries, dreaming_run(), dreaming_run(dreaming_version="cb0987654321")],
        r"more than one dreaming version: \['ba9876543210', 'cb0987654321'\]",
    ),
    "a failed dreaming run by another version": (
        lambda entries: [
            *entries,
            dreaming_run(dreaming_version="cb0987654321", **FAILED),
            dreaming_run(),
        ],
        "more than one dreaming version",
    ),
    "a replay run after a dreaming run": (
        lambda entries: [*entries, dreaming_run(), entries[-1]],
        "a replay run after a dreaming run",
    ),
    "a dreaming run of a wiki another version built": (
        lambda entries: [*entries, dreaming_run(librarian_version="cb0987654321")],
        "a dreaming run of a wiki built by librarian version cb0987654321, not 0123456789ab",
    ),
    "dreaming runs with no replay before them": (
        lambda entries: [dreaming_run()],
        "no replay run",
    ),
}
"""Run logs that aren't one replay's runs then one version's dreaming runs: how to make one
from the fixture's, and why it's refused."""


@pytest.mark.parametrize(("change", "reason"), MIXED.values(), ids=list(MIXED))
def test_a_run_log_that_mixes_anything_else_is_refused(tmp_path, change, reason):
    directory = copy_of_the_fixture(tmp_path)
    rewrite_run_log(directory, change)

    with pytest.raises(EvalWikiError, match=reason):
        load_eval_wiki(directory)


def test_eval_wikis_are_kept_in_the_evals_tree_by_corpus_and_librarian_version():
    directory = frozen.eval_wiki("scale", "0123456789ab")

    assert directory.relative_to(WORKSPACE) == Path("evals/wikis/scale/0123456789ab")
