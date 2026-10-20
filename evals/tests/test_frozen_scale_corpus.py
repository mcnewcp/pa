"""Checks on the frozen scale corpus and its eval set: the Argus payloads, untouched, and the
extension's payloads beside them, asked with the Argus questions and the new ones."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from pa_core.envelope import Envelope
from pa_core.librarian.identity import identity_table
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

V0_12_QUESTIONS_SHA256 = "7c80704961bf819c7eb9da039513ed121c14e22ad73d3a13d154051ba6ad12d6"
"""The digest of the scale eval set's 40 questions as v0.12 asked them, evidence ids included."""

V0_12_1_QUESTIONS = [
    "overdue-sweep",
    "pending-decisions",
    "kitchen-stands",
    "swim-stands",
    "wedding-stands",
    "basement-stands",
]
"""The questions v0.12.1 adds, where the wiki should help."""


@pytest.fixture(scope="module")
def scale() -> StorylineSpec:
    return load_spec(frozen.SPEC, *frozen.SCALE_EXTENSION)


@pytest.fixture(scope="module")
def eval_set() -> EvalSet:
    return load_eval_set(frozen.SCALE_EVAL_SET)


@pytest.fixture(scope="module")
def scale_l0(eval_set: EvalSet) -> tuple[IngestCounts, list[Envelope]]:
    """The whole scale corpus ingested by the eval harness, and the envelopes in its L0."""
    envelopes: list[Envelope] = []

    class ListingAgent:
        def answer(self, request: AgentRequest) -> AgentAnswer:
            envelopes.extend(FilesystemL0(request.data_root.l0).envelopes())
            return AgentAnswer("answer")

    one_question = eval_set.only(eval_set.questions[0].id)
    results = run_eval(SCALE.payloads, one_question, ListingAgent(), ScriptedJudge({}))
    return results.ingest, envelopes


@pytest.fixture(scope="module")
def ingested(scale_l0: tuple[IngestCounts, list[Envelope]]) -> tuple[IngestCounts, set[str]]:
    """The whole scale corpus ingested by the eval harness, and the episode ids in its L0."""
    counts, envelopes = scale_l0
    return counts, {e.episode_id for e in envelopes}


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


def test_the_identity_table_groups_daves_and_bens_addresses_under_their_names(
    scale_l0, eval_set: EvalSet
):
    _, envelopes = scale_l0

    table = identity_table(envelopes, eval_set.owner.to_owner())

    people = {person.slug: person for person in table.people}
    assert (people["david-kim"].name, people["david-kim"].addresses) == (
        "David Kim",
        ("david.kim@kimlaw.example", "dkim@example.net"),
    )
    assert (people["ben-okafor"].name, people["ben-okafor"].addresses) == (
        "Ben Okafor",
        ("ben.okafor@example.com", "ben@okafor-studio.example"),
    )
    organizations = {org.slug: org for org in table.organizations}
    assert organizations["kimlaw"].members == ("david-kim",)
    assert "ben-okafor" in organizations["okafor-studio"].members
    # Every message from the owner's address in the corpus was sent from the owner's mailbox.
    assert table.forged == ()
    assert table.owner.addresses == ("argus@example.com",)
    assert not any(person.uses_owner_name for person in table.people)


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


def test_the_scale_eval_set_adds_connection_and_where_things_stand_questions(eval_set):
    counts = Counter(question.category for question in eval_set.questions)

    assert len(eval_set.questions) == 46
    assert counts["connection"] == 3
    assert counts["where_things_stand"] == 5


def test_the_v0_12_questions_are_asked_unchanged_and_v0_12_1s_follow_them(eval_set: EvalSet):
    v0_12 = [question.model_dump(mode="json") for question in eval_set.questions[:40]]
    digest = hashlib.sha256(json.dumps(v0_12, sort_keys=True).encode()).hexdigest()

    assert digest == V0_12_QUESTIONS_SHA256
    assert [question.id for question in eval_set.questions[40:]] == V0_12_1_QUESTIONS
