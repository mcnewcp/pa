import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import SqliteCatalog
from pa_core.envelope import Envelope
from pa_core.ingest import IngestSummary, ingest
from pa_core.model_client import FakeModelBackend, ModelClient, ModelRequest
from pa_evals.corpus import GeneratedCorpusError, evidence_ids, generate_corpus
from pa_evals.storyline import StorylineSpec, load_spec
from pa_home.claude_model import ClaudeCliBackend
from pa_home.filesystem_l0 import FilesystemL0

SAMPLE_SPEC = Path(__file__).parent / "fixtures" / "sample_spec.yaml"


def prose(request: ModelRequest) -> dict[str, str]:
    """Deterministic prose that differs for every prompt, with characters beyond ASCII."""
    digest = hashlib.sha256(request.prompt.encode()).hexdigest()[:8]
    return {"text": f"Some prose ({digest}) — at the café.\n\nA second paragraph."}


def fake_client() -> ModelClient:
    return ModelClient(FakeModelBackend.replying(prose))


@pytest.fixture
def spec() -> StorylineSpec:
    return load_spec(SAMPLE_SPEC)


@pytest.fixture
def corpus(tmp_path: Path, spec: StorylineSpec) -> Path:
    out = tmp_path / "corpus"
    generate_corpus(spec, fake_client(), out)
    return out


def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def ingest_corpus(corpus: Path, spec: StorylineSpec, data_root: Path) -> IngestSummary:
    inbox = data_root / "inbox"
    inbox.mkdir(parents=True)
    for payload in corpus.iterdir():
        (inbox / payload.name).write_bytes(payload.read_bytes())
    store = FilesystemL0(data_root / "l0")
    with SqliteCatalog(data_root / "catalog.sqlite") as catalog:
        return ingest(
            inbox,
            store,
            catalog,
            spec.owner_identity,
            now=lambda: datetime(2026, 11, 1, tzinfo=UTC),
        )


@pytest.fixture
def episodes(tmp_path: Path, corpus: Path, spec: StorylineSpec) -> list[Envelope]:
    summary = ingest_corpus(corpus, spec, tmp_path / "pa-data")
    assert summary.quarantined == 0
    return list(FilesystemL0(tmp_path / "pa-data" / "l0").envelopes())


def test_every_generated_payload_ingests_without_quarantine(
    tmp_path: Path, corpus: Path, spec: StorylineSpec
) -> None:
    summary = ingest_corpus(corpus, spec, tmp_path / "pa-data")

    # 3 emails, 4 calendar versions, 2 daily notes plus 1 correction.
    assert (summary.ingested, summary.unchanged, summary.quarantined) == (10, 0, 0)


def by_subject(episodes: list[Envelope], subject: str) -> list[Envelope]:
    return [episode for episode in episodes if episode.subject == subject]


def people(envelope: Envelope) -> list[tuple[str, str, str | None]]:
    return [(p.role, p.identifier, p.name) for p in envelope.participants]


CHICAGO = ZoneInfo("America/Chicago")


def test_email_metadata_comes_straight_from_the_spec(episodes: list[Envelope]) -> None:
    [hotel] = by_subject(episodes, "Hotel block for the wedding")

    assert hotel.occurred_at.start == datetime(2026, 10, 2, 21, 30, tzinfo=CHICAGO)
    assert people(hotel) == [
        ("sender", "david.kim@kimlaw.example", "David Kim"),
        ("recipient", "argus@example.com", "Argus McNevans"),
        ("recipient", "mara@example.com", "Mara McNevans"),
        ("cc", "dkim@example.net", "David Kim"),
    ]
    assert hotel.body.startswith("Some prose (")  # written by the model


def test_a_reply_is_threaded_under_the_message_it_answers(episodes: list[Envelope]) -> None:
    [opening] = by_subject(episodes, "Fall swim lessons registration is open")
    [reply] = by_subject(episodes, "Re: Fall swim lessons registration is open")

    assert reply.thread_ref == opening.native_id == opening.thread_ref
    assert reply.occurred_at.start == datetime(2026, 10, 1, 20, 40, tzinfo=CHICAGO)
    assert people(reply) == [
        ("sender", "argus@example.com", "Argus McNevans"),
        ("recipient", "aquatics@westsideymca.example", "Westside YMCA Aquatics"),
        ("cc", "mara@example.com", "Mara McNevans"),
    ]


def test_calendar_metadata_comes_straight_from_the_spec(episodes: list[Envelope]) -> None:
    [shift, moved, cancelled] = sorted(
        (e for e in episodes if e.subject == "Evening shift"),
        key=lambda e: (e.occurred_at.start, "cancelled" in e.body),
    )

    assert shift.calendar_name == moved.calendar_name == cancelled.calendar_name == "Mara Work"
    assert shift.native_id == moved.native_id == cancelled.native_id
    assert len({shift.episode_id, moved.episode_id, cancelled.episode_id}) == 3
    assert (shift.occurred_at.start, shift.occurred_at.end) == (
        datetime(2026, 10, 5, 15, 0, tzinfo=CHICAGO),
        datetime(2026, 10, 5, 23, 30, tzinfo=CHICAGO),
    )
    assert (moved.occurred_at.start, moved.occurred_at.end) == (
        datetime(2026, 10, 6, 15, 0, tzinfo=CHICAGO),
        datetime(2026, 10, 6, 23, 30, tzinfo=CHICAGO),
    )
    assert cancelled.occurred_at == moved.occurred_at
    assert shift.body == "Status: confirmed\nLocation: St. Luke's Hospital"
    assert cancelled.body == "Status: cancelled\nLocation: St. Luke's Hospital"
    assert people(cancelled) == [("organizer", "mara@example.com", "Mara McNevans")]


def test_an_all_day_event_covers_its_days_on_its_calendar(episodes: list[Envelope]) -> None:
    [wedding] = by_subject(episodes, "Dave & Priya's wedding")

    assert wedding.calendar_name == "Family"
    assert (wedding.occurred_at.start, wedding.occurred_at.end) == (
        datetime(2026, 10, 24, tzinfo=CHICAGO),
        datetime(2026, 10, 26, tzinfo=CHICAGO),
    )
    assert wedding.body == (
        "Status: confirmed\n\n"
        "Ceremony at 4pm; reception to follow at Café Rosé, a long walk away.\nDress: cocktail."
    )
    assert people(wedding) == [
        ("organizer", "argus@example.com", "Argus McNevans"),
        ("attendee", "mara@example.com", "Mara McNevans"),
    ]


def notes_for(episodes: list[Envelope], path: str) -> list[Envelope]:
    return [e for e in episodes if e.thread_ref == path]


def headings(note: Envelope) -> list[str]:
    return [line for line in note.body.splitlines() if line.startswith("## ")]


def test_a_daily_note_has_one_heading_per_topic_for_its_day(episodes: list[Envelope]) -> None:
    [note] = notes_for(episodes, "Daily/2026-10-01.md")

    assert headings(note) == ["## Swim lessons"]
    assert (note.occurred_at.start, note.occurred_at.end) == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert people(note) == [("author", "argus@example.com", "Argus McNevans")]


def test_a_correction_is_a_new_version_of_the_whole_note(episodes: list[Envelope]) -> None:
    first, second = notes_for(episodes, "Daily/2026-10-03.md")

    assert headings(first) == headings(second) == ["## Swim lessons", "## Dave's wedding"]
    assert first.episode_id != second.episode_id
    # Only the corrected section, Swim lessons, differs.
    _, first_swim, first_wedding = first.body.split("## ")
    _, second_swim, second_wedding = second.body.split("## ")
    assert first_swim != second_swim
    assert first_wedding == second_wedding


def test_the_same_spec_and_model_generate_the_same_corpus(
    tmp_path: Path, spec: StorylineSpec, corpus: Path
) -> None:
    again = tmp_path / "again"
    generate_corpus(spec, fake_client(), again)

    assert snapshot(again) == snapshot(corpus)


def test_evidence_ids_name_the_episodes_ingest_creates(
    tmp_path: Path, spec: StorylineSpec, corpus: Path, episodes: list[Envelope]
) -> None:
    ids = evidence_ids(spec, corpus)
    by_id = {episode.episode_id: episode for episode in episodes}
    raw = FilesystemL0(tmp_path / "pa-data" / "l0").get_raw

    assert set(ids) == {event.id for event in spec.events}
    assert set(ids.values()) == set(by_id)
    assert by_id[ids["wedding-hotel-block"]].subject == "Hotel block for the wedding"
    moved = by_id[ids["shift-oct-5-moved"]]
    assert moved.occurred_at.start == datetime(2026, 10, 6, 15, tzinfo=CHICAGO)
    assert moved.body.startswith("Status: confirmed")
    assert by_id[ids["shift-oct-5-cancelled"]].body.startswith("Status: cancelled")
    # Sections of one day are in that day's note; a correction is in the note it produces.
    assert ids["swim-note-saturdays"] == ids["wedding-note"]
    original, corrected = by_id[ids["swim-note-saturdays"]], by_id[ids["swim-note-correction"]]
    assert original.thread_ref == corrected.thread_ref == "Daily/2026-10-03.md"
    assert b"Captured-At: 2026-10-04T01:00:00-05:00\n" in (raw(original.raw_ref) or b"")
    assert b"Captured-At: 2026-10-04T08:00:00-05:00\n" in (raw(corrected.raw_ref) or b"")


def test_the_model_is_asked_for_prose_about_what_happens(
    spec: StorylineSpec, tmp_path: Path
) -> None:
    backend = FakeModelBackend.replying(prose)
    generate_corpus(spec, ModelClient(backend), tmp_path / "corpus")
    prompts = [request.prompt for request in backend.requests]

    # One request per email, daily note section, and correction.
    assert len(prompts) == 3 + 3 + 1
    [hotel] = [p for p in prompts if "Dave says the hotel block closes on Oct 9." in p]
    assert "David Kim <david.kim@kimlaw.example>, known to friends and family as Dave" in hotel
    [reply] = [p for p in prompts if "same Saturday session" in p]
    [opening] = [r for r in backend.requests if "registration closes Oct 9" in r.prompt]
    assert prose(opening)["text"] in reply  # a reply follows on from the message it answers
    [correction] = [p for p in prompts if "starts at 9:00, not 10:00" in p]
    [saturdays] = [r for r in backend.requests if "Mara now prefers Saturdays" in r.prompt]
    assert prose(saturdays)["text"] in correction  # the section being corrected


def test_the_corpus_is_only_written_to_an_empty_directory(
    spec: StorylineSpec, corpus: Path
) -> None:
    before = snapshot(corpus)

    with pytest.raises(GeneratedCorpusError, match="is not empty"):
        generate_corpus(spec, fake_client(), corpus)
    assert snapshot(corpus) == before


def test_evidence_ids_need_every_payload(spec: StorylineSpec, corpus: Path) -> None:
    next(corpus.glob("*_wedding-day.ics")).unlink()

    with pytest.raises(GeneratedCorpusError, match="event wedding-day: its payload"):
        evidence_ids(spec, corpus)


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_a_corpus_written_by_the_real_claude_cli_ingests_cleanly(
    tmp_path: Path, spec: StorylineSpec
) -> None:
    model = os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku")
    corpus = tmp_path / "corpus"
    generate_corpus(spec, ModelClient(ClaudeCliBackend(model=model)), corpus)

    summary = ingest_corpus(corpus, spec, tmp_path / "pa-data")

    assert summary.quarantined == 0
    episodes = {e.episode_id for e in FilesystemL0(tmp_path / "pa-data" / "l0").envelopes()}
    assert set(evidence_ids(spec, corpus).values()) == episodes
