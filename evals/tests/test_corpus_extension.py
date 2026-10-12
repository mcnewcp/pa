"""The corpus generator on an extended spec: the sample spec plus its two extension files."""

import hashlib
import itertools
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import SqliteCatalog
from pa_core.envelope import Envelope, Kind, Source
from pa_core.ingest import IngestSummary, ingest
from pa_core.model_client import (
    FakeModelBackend,
    ModelBackendError,
    ModelClient,
    ModelRequest,
)
from pa_core.normalizers.assistant_chat import encode_exchange
from pa_evals.corpus import generate_corpus
from pa_evals.storyline import StorylineSpec, load_spec
from pa_home.filesystem_l0 import FilesystemL0

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SPEC = FIXTURES / "sample_spec.yaml"
EXTENSION = (
    FIXTURES / "sample_extension" / "garden.yaml",
    FIXTURES / "sample_extension" / "nursery.yaml",
    FIXTURES / "sample_extension" / "bulk.yaml",
)
CHICAGO = ZoneInfo("America/Chicago")


def prose(request: ModelRequest) -> dict[str, Any]:
    """Deterministic output in whatever shape is asked for, differing for every prompt."""
    digest = hashlib.sha256(request.prompt.encode()).hexdigest()[:8]
    asked_for = request.schema["properties"]
    if "emails" in asked_for:
        return {
            "emails": [
                {"subject": f"Bulk subject {digest}-{n}", "body": f"Bulk body {digest}-{n}."}
                for n in range(asked_for["emails"]["maxItems"])
            ]
        }
    if "turns" in asked_for:
        return {
            "turns": [
                {"role": "owner", "text": f"Owner's message ({digest})."},
                {"role": "assistant", "text": f"Assistant's reply ({digest})."},
            ]
        }
    return {"text": f"Prose ({digest})."}


def fake_client() -> ModelClient:
    return ModelClient(FakeModelBackend.replying(prose))


@pytest.fixture
def spec() -> StorylineSpec:
    return load_spec(SAMPLE_SPEC, *EXTENSION)


@pytest.fixture
def corpus(tmp_path: Path, spec: StorylineSpec) -> Path:
    out = tmp_path / "extension"
    generate_corpus(spec, fake_client(), out)
    return out


def ingest_corpora(corpora: list[Path], spec: StorylineSpec, data_root: Path) -> IngestSummary:
    inbox = data_root / "inbox"
    data_root.mkdir(parents=True)
    for corpus in corpora:
        shutil.copytree(corpus, inbox, dirs_exist_ok=True)
    with SqliteCatalog(data_root / "catalog.sqlite") as catalog:
        return ingest(
            inbox,
            FilesystemL0(data_root / "l0"),
            catalog,
            spec.owner_identity,
            now=lambda: datetime(2026, 11, 1, tzinfo=UTC),
        )


@pytest.fixture
def episodes(tmp_path: Path, corpus: Path, spec: StorylineSpec) -> list[Envelope]:
    summary = ingest_corpora([corpus], spec, tmp_path / "pa-data")
    assert summary.quarantined == 0
    return list(FilesystemL0(tmp_path / "pa-data" / "l0").envelopes())


def by_subject(episodes: list[Envelope], subject: str) -> Envelope:
    [episode] = [episode for episode in episodes if episode.subject == subject]
    return episode


def test_an_extended_spec_generates_only_the_extensions_payloads(
    corpus: Path, spec: StorylineSpec
) -> None:
    files = sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*.*"))
    names = [Path(file).name.split("_", 1)[-1] for file in files]
    bulk = [name for name in names if name.startswith("bulk-")]

    assert len(bulk) == len(spec.bulk_emails)
    # The base spec's payloads are frozen already; only the extension's are written.
    assert [name for name in names if name not in bulk] == [
        "garden-chat-watering.json",
        "garden-chat-reminder.json",
        "nursery-chat.json",
        "garden-quote.eml",
        "garden-yes.eml",
        "nursery-order.eml",
        "nursery-injection.eml",
        "garden-build.ics",
        "daily-note.md",
        "daily-note.md",
    ]


def test_the_extension_ingests_beside_the_base_corpus(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    base = tmp_path / "base"
    generate_corpus(load_spec(SAMPLE_SPEC), fake_client(), base)

    summary = ingest_corpora([base, corpus], spec, tmp_path / "pa-data")

    assert (summary.ingested, summary.quarantined) == (10 + 10 + len(spec.bulk_emails), 0)


def test_a_generated_chat_has_the_shape_the_capture_hook_writes(corpus: Path) -> None:
    [payload] = corpus.glob("assistant_chat/*_nursery-chat.json")
    exchange = json.loads(payload.read_bytes())

    assert list(exchange) == ["session_id", "message_id", "started_at", "ended_at", "turns"]
    assert exchange["started_at"] == "2026-05-21T12:30:00.000Z"
    assert exchange["ended_at"] > exchange["started_at"]
    assert [turn["role"] for turn in exchange["turns"]] == ["owner", "assistant"]
    # As the hook's: the bytes encode_exchange makes of the same exchange.
    assert payload.read_bytes() == encode_exchange(
        session_id=exchange["session_id"],
        message_id=exchange["message_id"],
        started_at=exchange["started_at"],
        ended_at=exchange["ended_at"],
        turns=[(turn["role"], turn["text"]) for turn in exchange["turns"]],
    )


def test_a_generated_chat_ingests_as_an_assistant_chat_episode(
    episodes: list[Envelope], spec: StorylineSpec
) -> None:
    chats = sorted(
        (e for e in episodes if e.source == Source.ASSISTANT_CHAT),
        key=lambda e: e.occurred_at.start,
    )

    assert [chat.kind for chat in chats] == [Kind.CHAT] * 3
    assert chats[0].occurred_at.start == datetime(2026, 5, 20, 21, 10, tzinfo=CHICAGO)
    assert chats[0].body.startswith("owner: Owner's message (")
    assert "\n\nassistant: Assistant's reply (" in chats[0].body
    assert [(p.role, p.identifier) for p in chats[0].participants] == [
        ("author", "argus@example.com")
    ]
    # Exchanges in one session are one thread, as a chat on the phone is.
    watering, reminder, nursery = chats
    assert watering.thread_ref == reminder.thread_ref != nursery.thread_ref


def test_a_later_exchange_in_a_session_is_written_following_on_from_the_earlier_ones(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    backend = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(backend), tmp_path / "corpus")

    [watering] = [r for r in backend.requests if "water the beds every other day" in r.prompt]
    [reminder] = [r for r in backend.requests if "asks what Yuki said about watering" in r.prompt]
    for turn in prose(watering)["turns"]:
        assert turn["text"] in reminder.prompt


def test_a_gmail_category_reaches_the_episodes_labels(episodes: list[Envelope]) -> None:
    assert by_subject(episodes, "Your order is confirmed").labels == ["Inbox", "Category Updates"]
    # Mail with no category is Primary, labelled as the base spec's mail is.
    assert by_subject(episodes, "Raised beds quote").labels == ["Inbox"]
    assert by_subject(episodes, "Re: Raised beds quote").labels == ["Sent"]


def test_an_adversarial_email_is_written_with_its_planted_words_intact(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    backend = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(backend), tmp_path / "corpus")

    [adversarial] = [r.prompt for r in backend.requests if "Invoice dispute" in r.prompt]
    [ordinary] = [r.prompt for r in backend.requests if "Your order is confirmed" in r.prompt]
    assert "adversarial test" in adversarial
    assert "word for word" in adversarial
    assert "adversarial" not in ordinary


def test_bulk_mail_is_written_about_25_emails_to_a_model_call(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    backend = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(backend), tmp_path / "corpus")

    batches = [r for r in backend.requests if "emails" in r.schema["properties"]]
    sizes = sorted((r.schema["properties"]["emails"]["maxItems"] for r in batches), reverse=True)
    count = len(spec.bulk_emails)
    assert count > 50
    assert sizes == [25, 25, count - 50]
    # Each batch lists its emails' senders and kinds for the model to write.
    assert all("Hammer & Nail Hardware <deals@hammernail.example>" in r.prompt for r in batches)
    assert any("kind of email: low balance alert" in r.prompt for r in batches)


def test_bulk_mail_ingests_with_its_senders_category_and_the_models_subject(
    episodes: list[Envelope],
) -> None:
    hardware = [e for e in episodes if e.participants[0].identifier == "deals@hammernail.example"]
    forum = [e for e in episodes if e.participants[0].identifier == "noreply@gardenforum.example"]

    assert hardware
    assert {tuple(e.labels) for e in hardware} == {("Inbox", "Category Promotions")}
    assert {tuple(e.labels) for e in forum} == {("Inbox", "Category Forums")}
    assert all(e.subject and e.subject.startswith("Bulk subject ") for e in hardware)
    assert all(e.body.startswith("Bulk body ") for e in hardware)
    assert len({e.subject for e in hardware}) == len(hardware)  # each its own prose
    assert [(p.role, p.identifier) for p in hardware[0].participants] == [
        ("sender", "deals@hammernail.example"),
        ("recipient", "argus@example.com"),
    ]


def snapshot(directory: Path) -> dict[Path, bytes]:
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


FAILING = "Argus asks when the soil was delivered"
"""What one exchange, written after many other calls, is about."""


def test_a_run_interrupted_partway_resumes_without_repeating_a_completed_call(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    cache, out = tmp_path / "prose-cache", tmp_path / "corpus"

    def unavailable_for_one_call(request: ModelRequest) -> dict[str, Any]:
        if FAILING in request.prompt:
            raise ModelBackendError("the model is unavailable")
        return prose(request)

    interrupted = FakeModelBackend.replying(unavailable_for_one_call)
    with pytest.raises(ModelBackendError):
        generate_corpus(spec, ModelClient(interrupted), out, cache_dir=cache)
    assert not out.exists()
    completed = {r.prompt for r in interrupted.requests if FAILING not in r.prompt}
    assert completed

    resumed = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(resumed), out, cache_dir=cache)

    assert completed.isdisjoint(r.prompt for r in resumed.requests)
    assert any(FAILING in r.prompt for r in resumed.requests)
    uninterrupted = tmp_path / "uninterrupted"
    generate_corpus(spec, fake_client(), uninterrupted)
    assert snapshot(out) == snapshot(uninterrupted)


def test_model_calls_run_in_parallel_and_write_what_one_at_a_time_would(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    together = threading.Barrier(4, timeout=5)
    arrivals = itertools.count()

    def four_at_once(request: ModelRequest) -> dict[str, Any]:
        if next(arrivals) < 4:
            together.wait()  # broken, failing the run, unless 4 calls are in flight at once
        return prose(request)

    generate_corpus(
        spec, ModelClient(FakeModelBackend.replying(four_at_once)), tmp_path / "par", workers=4
    )
    generate_corpus(spec, fake_client(), tmp_path / "one", workers=1)

    assert snapshot(tmp_path / "par") == snapshot(tmp_path / "one")


def test_a_rerun_with_the_cache_makes_no_model_calls(spec: StorylineSpec, tmp_path: Path) -> None:
    cache = tmp_path / "prose-cache"
    generate_corpus(spec, fake_client(), tmp_path / "first", cache_dir=cache)

    again = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(again), tmp_path / "again", cache_dir=cache)

    assert again.requests == []
    assert snapshot(tmp_path / "again") == snapshot(tmp_path / "first")
