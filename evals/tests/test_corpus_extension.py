"""The corpus generator on an extended spec: the sample spec plus its two extension files."""

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pa_core.catalog import SqliteCatalog
from pa_core.envelope import Envelope
from pa_core.ingest import IngestSummary, ingest
from pa_core.model_client import FakeModelBackend, ModelClient, ModelRequest
from pa_evals.corpus import generate_corpus
from pa_evals.storyline import StorylineSpec, load_spec
from pa_home.filesystem_l0 import FilesystemL0

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_SPEC = FIXTURES / "sample_spec.yaml"
EXTENSION = (
    FIXTURES / "sample_extension" / "garden.yaml",
    FIXTURES / "sample_extension" / "nursery.yaml",
)


def prose(request: ModelRequest) -> dict[str, Any]:
    """Deterministic output in whatever shape is asked for, differing for every prompt."""
    digest = hashlib.sha256(request.prompt.encode()).hexdigest()[:8]
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


def test_an_extended_spec_generates_only_the_extensions_payloads(corpus: Path) -> None:
    files = sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*.*"))

    # The base spec's payloads are frozen already; only the extension's are written.
    assert [Path(file).name.split("_", 1)[-1] for file in files] == [
        "garden-quote.eml",
        "garden-yes.eml",
        "nursery-order.eml",
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

    assert (summary.ingested, summary.quarantined) == (10 + 6, 0)


def test_a_gmail_category_reaches_the_episodes_labels(episodes: list[Envelope]) -> None:
    assert by_subject(episodes, "Your order is confirmed").labels == ["Inbox", "Category Updates"]
    # Mail with no category is Primary, labelled as the base spec's mail is.
    assert by_subject(episodes, "Raised beds quote").labels == ["Inbox"]
    assert by_subject(episodes, "Re: Raised beds quote").labels == ["Sent"]
