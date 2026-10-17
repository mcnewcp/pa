"""Checks on the frozen scale corpus and its eval set: the Argus payloads, untouched, and the
extension's payloads beside them, asked with the Argus questions and the new ones."""

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from pa_evals import frozen
from pa_evals.agent import AgentAnswer, AgentRequest
from pa_evals.corpus import payload_path
from pa_evals.eval_set import EvalSet, load_eval_set
from pa_evals.harness import run_eval
from pa_evals.judge import ScriptedJudge
from pa_evals.questions import build_eval_set, load_questions
from pa_evals.results import IngestCounts
from pa_evals.storyline import StorylineSpec, load_spec
from pa_home.filesystem_l0 import FilesystemL0

SCALE = frozen.CORPORA["scale"]

ARGUS_PAYLOADS_SHA256 = "49a4ab1a6894854b3fcd1171033570b45431ae1f00f2e62170ca64fb4ec35379"
"""The digest of every Argus payload's path and bytes, taken when the scale corpus was frozen."""


@pytest.fixture(scope="module")
def scale() -> StorylineSpec:
    return load_spec(frozen.SPEC, *frozen.SCALE_EXTENSION)


@pytest.fixture(scope="module")
def eval_set() -> EvalSet:
    return load_eval_set(frozen.SCALE_EVAL_SET)


@pytest.fixture(scope="module")
def ingested(eval_set: EvalSet) -> tuple[IngestCounts, set[str]]:
    """The whole scale corpus ingested by the eval harness, and the episode ids in its L0."""
    episodes: set[str] = set()

    class ListingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            episodes.update(e.episode_id for e in FilesystemL0(request.data_root.l0).envelopes())
            return AgentAnswer("answer")

    one_question = eval_set.only(eval_set.questions[0].id)
    results = run_eval(SCALE.payloads, one_question, ListingAgent(), ScriptedJudge({}))
    return results.ingest, episodes


def files(directory: Path) -> list[Path]:
    return sorted(p.relative_to(directory) for p in directory.rglob("*") if p.is_file())


def test_the_argus_payloads_are_byte_for_byte_as_they_were(scale):
    digest = hashlib.sha256()
    for path in files(frozen.PAYLOADS):
        digest.update(path.as_posix().encode() + b"\0" + (frozen.PAYLOADS / path).read_bytes())

    assert digest.hexdigest() == ARGUS_PAYLOADS_SHA256


def test_the_scale_corpus_is_the_argus_payloads_and_the_extensions(scale):
    assert SCALE.payloads == (frozen.PAYLOADS, frozen.SCALE_PAYLOADS)
    extension = {payload_path(scale, e) for e in scale.events if not scale.in_base(e.id)}
    assert {path.as_posix() for path in files(frozen.SCALE_PAYLOADS)} == extension
    assert extension.isdisjoint(path.as_posix() for path in files(frozen.PAYLOADS))


def test_ingesting_the_scale_corpus_lands_every_payload_and_quarantines_nothing(ingested):
    counts, episodes = ingested
    payloads = len(files(frozen.PAYLOADS)) + len(files(frozen.SCALE_PAYLOADS))

    assert counts.quarantined == 0
    assert counts.ingested == payloads == len(episodes)
    assert 1150 <= payloads <= 1300


def test_every_evidence_id_in_the_scale_eval_set_exists_in_l0(ingested, eval_set: EvalSet):
    _, episodes = ingested

    for question in eval_set.questions:
        missing = set(question.evidence) - episodes
        assert not missing, f"{question.id} needs episodes that are not in L0: {missing}"


def test_the_adversarial_emails_are_listed_and_in_l0(ingested, eval_set: EvalSet):
    _, episodes = ingested

    assert len(eval_set.adversarial) == 5
    assert set(eval_set.adversarial) <= episodes


def test_the_scale_eval_set_is_exactly_what_the_questions_build_into(scale, eval_set: EvalSet):
    questions = [frozen.QUESTIONS, frozen.SCALE_QUESTIONS]

    assert eval_set == build_eval_set(questions, scale, SCALE.payloads)


def test_the_argus_questions_are_asked_unchanged_with_their_episode_ids(eval_set: EvalSet):
    argus = load_eval_set(frozen.EVAL_SET)
    new = load_questions(frozen.SCALE_QUESTIONS).questions

    assert eval_set.questions == argus.questions + eval_set.questions[len(argus.questions) :]
    assert [q.id for q in eval_set.questions[len(argus.questions) :]] == [q.id for q in new]
    assert (eval_set.as_of, eval_set.owner, eval_set.time_zone) == (
        argus.as_of,
        argus.owner,
        argus.time_zone,
    )


def test_the_scale_eval_set_adds_connection_questions_to_the_argus_categories(eval_set):
    counts = Counter(question.category for question in eval_set.questions)

    assert len(eval_set.questions) == 40
    assert counts["connection"] == 3
