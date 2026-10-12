"""The librarian and the PA MCP server, end to end at one seam: fixture L0 and a scripted
model in, `rebuild`, then answers through an in-process MCP client and the build report."""

from __future__ import annotations

import gc
import json
import os
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import pytest
from mcp import Client, StdioServerParameters

from pa_core.envelope import Envelope
from pa_core.librarian import extraction, topics
from pa_core.librarian.ports import LibrarianModels, ModelChoice
from pa_core.librarian.rebuild import LibrarianError
from pa_core.librarian.report import BuildReport
from pa_core.model_client import FakeModelBackend, ModelBackendError, ModelRequest
from pa_core.normalizers.assistant_chat import (
    TurnRole,
    encode_exchange,
    normalize_assistant_chat,
)
from pa_core.normalizers.calendar import normalize_calendar_event
from pa_core.normalizers.daily_note import normalize_daily_note
from pa_core.normalizers.email import normalize_email
from pa_core.owner import Owner
from pa_home import cli
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.graph import extensions
from pa_home.librarian import rebuild_graph
from pa_home.mcp_server import graph_server

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
ARGUS = "Argus McNevans <argus@example.com>"
DAVE = "David Kim <dkim@example.net>"
DAVE_ADDRESS = "dkim@example.net"
MARIA = "Maria Alvarez <maria@alvarezkb.example>"
RICK = "Rick Alvarez <rick@alvarezkb.example>"
MARA = "Mara McNevans <mara@example.com>"
CHICAGO = ZoneInfo("America/Chicago")


class Mailbox:
    """Fixture L0: emails normalized and stored as ingest would store them."""

    def __init__(self, l0_root: Path) -> None:
        self.l0 = FilesystemL0(l0_root)
        self._sent = 0

    def receive(
        self,
        body: str,
        *,
        sender: str = DAVE,
        to: str = ARGUS,
        subject: str = "Hello",
        replying_to: str | None = None,
        day: int | None = None,
        labels: str | None = None,
    ) -> str:
        """Store an email and return its episode id (also usable as its thread's id).

        `labels` is the `X-Gmail-Labels` header, as a Takeout export writes it."""
        self._sent += 1
        message_id = f"m{self._sent}@example.test"
        headers = [
            f"Message-ID: <{message_id}>",
            f"Date: {day or self._sent:02d} Sep 2026 10:00:00 -0500",
            f"From: {sender}",
            f"To: {to}",
            f"Subject: {subject}",
        ]
        if replying_to:
            root = self.l0.get(replying_to)
            assert root is not None
            headers.append(f"In-Reply-To: <{root.thread_ref}>")
            headers.append(f"References: <{root.thread_ref}>")
        if labels:
            headers.append(f"X-Gmail-Labels: {labels}")
        raw = (
            "\r\n".join([*headers, 'Content-Type: text/plain; charset="utf-8"', "", body]) + "\r\n"
        ).encode()
        envelope = normalize_email(raw, captured_at=datetime(2026, 10, 1, tzinfo=UTC))
        self.l0.put(envelope, raw)
        return envelope.episode_id

    def note(self, text: str, *, day: int) -> str:
        """Store the owner's daily note for a day in September and return its episode id."""
        raw = f"Vault-Path: Daily/2026-09-{day:02d}.md\n\n{text}".encode()
        envelope = normalize_daily_note(
            raw, captured_at=datetime(2026, 10, 1, tzinfo=UTC), owner=OWNER
        )
        self.l0.put(envelope, raw)
        return envelope.episode_id

    def chat(self, *turns: tuple[TurnRole, str], session: str = "session-1", day: int) -> str:
        """Store an exchange with the assistant, as the capture hook writes it."""
        self._sent += 1
        at = f"2026-09-{day:02d}T15:00:00.000Z"
        raw = encode_exchange(
            session_id=session,
            message_id=f"message-{self._sent}",
            started_at=at,
            ended_at=at,
            turns=turns,
        )
        envelope = normalize_assistant_chat(
            raw, captured_at=datetime(2026, 10, 1, tzinfo=UTC), owner=OWNER
        )
        self.l0.put(envelope, raw)
        return envelope.episode_id

    def event(
        self,
        title: str,
        *,
        uid: str = "appointment",
        starts: str = "2026-10-08T07:30",
        ends: str = "2026-10-08T08:00",
        changed: str = "2026-09-16T10:00",
        sequence: int = 0,
        organizer: str = ARGUS,
        attendees: tuple[str, ...] = (),
        location: str | None = None,
        status: str = "CONFIRMED",
        description: str = "",
    ) -> str:
        """Store one version of a calendar event, as iCloud exports it, and return its episode
        id. Times are in America/Chicago; `changed` is when this version was made."""

        def stamp(moment: str) -> str:
            return moment.replace("-", "").replace(":", "") + "00"

        def who(mailbox: str) -> str:
            name, address = mailbox.rstrip(">").split(" <")
            return f"CN={name}:mailto:{address}"

        changed_utc = datetime.fromisoformat(changed).replace(tzinfo=CHICAGO).astimezone(UTC)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "X-WR-CALNAME:Personal",
            "X-WR-TIMEZONE:America/Chicago",
            "BEGIN:VEVENT",
            f"UID:{uid}@example.test",
            f"SEQUENCE:{sequence}",
            f"DTSTAMP:{changed_utc:%Y%m%dT%H%M%SZ}",
            f"LAST-MODIFIED:{changed_utc:%Y%m%dT%H%M%SZ}",
            f"DTSTART;TZID=America/Chicago:{stamp(starts)}",
            f"DTEND;TZID=America/Chicago:{stamp(ends)}",
            f"SUMMARY:{title}",
            *([f"LOCATION:{location}"] if location else []),
            *([f"DESCRIPTION:{description}"] if description else []),
            f"ORGANIZER;{who(organizer)}",
            *(f"ATTENDEE;{who(attendee)}" for attendee in attendees),
            f"STATUS:{status}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
        raw = ("\r\n".join(lines) + "\r\n").encode()
        envelope = normalize_calendar_event(raw, captured_at=datetime(2026, 10, 1, tzinfo=UTC))
        self.l0.put(envelope, raw)
        return envelope.episode_id

    def edit(self, episode_id: str, body: str) -> None:
        """Change an episode's body, as a renormalize that changed its envelope would."""
        changed: list[Envelope] = []
        for envelope in self.l0.envelopes():
            if envelope.episode_id == episode_id:
                fields = envelope.model_dump(exclude={"content_hash"})
                envelope = Envelope.seal(**{**fields, "body": body})
            changed.append(envelope)
        self.l0.replace_envelopes(changed)


def statement(
    quote: str,
    kind: str = "mentioned",
    speaker: str = "",
    summary: str = "",
    topics: tuple[str, ...] = (),
) -> dict:
    return {
        "speaker": speaker or "the sender",
        "kind": kind,
        "summary": summary or f"Said: {quote}",
        "quote": quote,
        "topics": list(topics),
    }


@dataclass
class ScriptedModel:
    """Replies to an extraction prompt by the scripted phrase found last in it: the episode
    comes after its thread's earlier messages, so its own phrase is the last one. Replies to a
    triage relevance check by the phrase found in it (unscripted mail is not relevant).

    Asked to place topic names, it places each as scripted with `places`; a name it has no
    script for becomes a new topic of that name."""

    replies: dict[str, list[dict]] = field(default_factory=dict)
    relevance: dict[str, bool] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    answered: list[str] = field(default_factory=list)
    judged: list[str] = field(default_factory=list)
    placements: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    placed: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.backend = FakeModelBackend.replying(self._reply)

    def says(self, phrase: str, *statements: dict) -> None:
        self.replies[phrase] = list(statements)

    def judges(self, phrase: str, *, relevant: bool) -> None:
        self.relevance[phrase] = relevant

    def places(self, name: str, *, under: str = "", new: str = "", description: str = "") -> None:
        """Script where the topic name `name` goes: `under` the existing topic of that name,
        or into a `new` topic with `description`."""
        self.placements[name.lower()] = (under, None) if under else (new or name, description)

    @property
    def topic_calls(self) -> int:
        return len(self.placed)

    @property
    def calls(self) -> int:
        return len(self.backend.requests)

    @property
    def prompts(self) -> list[str]:
        return [request.prompt for request in self.backend.requests]

    def _reply(self, request: ModelRequest) -> dict[str, Any]:
        if request.schema == topics.TopicPlacements.model_json_schema():
            return self._place(request)
        if "relevant" in request.schema["properties"]:
            return self._judge(request)
        present = [phrase for phrase in self.replies if phrase in request.prompt]
        if not present:
            return {"statements": []}
        phrase = max(present, key=request.prompt.rfind)
        if phrase in self.failing:
            raise ModelBackendError(f"scripted failure for {phrase!r}")
        self.answered.append(phrase)
        return {"statements": self.replies[phrase]}

    def _judge(self, request: ModelRequest) -> dict[str, Any]:
        present = [phrase for phrase in self.relevance if phrase in request.prompt]
        if not present:
            return {"relevant": False, "reason": "unscripted"}
        (phrase,) = present
        if phrase in self.failing:
            raise ModelBackendError(f"scripted failure for {phrase!r}")
        self.judged.append(phrase)
        return {"relevant": self.relevance[phrase], "reason": "scripted"}

    def _place(self, request: ModelRequest) -> dict[str, Any]:
        asked = json.loads(request.prompt.split("<topic_input>")[1].split("</topic_input>")[0])
        names: list[str] = asked["names"]
        if any(name in self.failing for name in names):
            raise ModelBackendError(f"scripted failure placing {names}")
        self.placed.append(names)
        by_name = {c["name"]: c["id"] for c in asked["candidates"]}
        placements = []
        for name in names:
            target, description = self.placements.get(name.lower(), (name, f"About {name}."))
            if description is None:
                assert target in by_name, f"{target!r} is not among the candidates {by_name}"
                placements.append({"name": name, "topic": by_name[target]})
            else:
                placements.append(
                    {"name": name, "topic": "new", "new_name": target, "description": description}
                )
        return {"placements": placements}


class Librarian:
    def __init__(self, root: Path, extensions_root: Path) -> None:
        self.data_root = DataRoot(root)
        self.mail = Mailbox(self.data_root.l0)
        self.model = ScriptedModel()
        self.models = LibrarianModels()
        self.extensions_root = extensions_root
        self.backends: list[ModelChoice] = []
        self.server = graph_server(self.data_root.graph, extensions_root=extensions_root)

    def rebuild(self) -> BuildReport:
        def backend_for(choice: ModelChoice) -> FakeModelBackend:
            self.backends.append(choice)
            return self.model.backend

        return rebuild_graph(
            self.data_root,
            OWNER,
            models=self.models,
            backend_for=backend_for,
            extensions_root=self.extensions_root,
        )

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        """A tool's structured result, through an in-process MCP client."""

        async def go() -> dict[str, Any]:
            async with Client(self.server) as client:
                result = await client.call_tool(tool, arguments)
            assert not result.is_error, result.content
            assert result.structured_content is not None
            return result.structured_content

        return anyio.run(go)

    def call_failing(self, tool: str, **arguments: Any) -> str:
        """The error text of a tool call that fails."""

        async def go() -> str:
            async with Client(self.server) as client:
                result = await client.call_tool(tool, arguments)
            assert result.is_error
            return " ".join(getattr(block, "text", "") for block in result.content)

        return anyio.run(go)


def nodes(result: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [node for node in result["nodes"] if node["type"] == node_type]


def quotes(result: dict[str, Any]) -> list[str]:
    return [node["properties"]["quote"] for node in nodes(result, "Statement")]


def speaker_of(result: dict[str, Any], statement_id: str) -> dict[str, Any]:
    (said,) = [e for e in result["edges"] if e["type"] == "SAID" and e["target"] == statement_id]
    return next(node for node in result["nodes"] if node["id"] == said["source"])


@pytest.fixture(scope="session")
def extensions_root() -> Path:
    root = extensions.extensions_dir()
    extensions.ensure(root, extensions.FTS)
    return root


@pytest.fixture
def librarian(tmp_path: Path, extensions_root: Path) -> Iterator[Librarian]:
    librarian = Librarian(tmp_path / "pa-data", extensions_root)
    yield librarian
    # Each open build reserves 8 TiB of address space, so a run of tests that leaves its
    # readers to the garbage collector can run out of it. Collecting the server closes them.
    del librarian.server
    gc.collect()


def test_a_firsthand_statement_is_the_senders_even_when_the_model_names_someone_else(
    librarian: Librarian,
):
    librarian.mail.receive("Would you read at the wedding? It would mean a lot.", sender=DAVE)
    librarian.model.says(
        "Would you read at the wedding",
        statement("Would you read at the wedding?", kind="asked", speaker="Argus McNevans"),
    )

    librarian.rebuild()

    by_dave = librarian.call("statements", who="dkim@example.net")
    assert quotes(by_dave) == ["Would you read at the wedding?"]
    (said,) = [n["id"] for n in by_dave["nodes"] if n["type"] == "Statement"]
    assert speaker_of(by_dave, said)["properties"]["addresses"] == ["dkim@example.net"]
    assert quotes(librarian.call("statements", who="argus@example.com")) == []


def test_a_statement_whose_quote_is_not_in_the_body_is_dropped_and_counted(librarian: Librarian):
    librarian.mail.receive("The tile arrives Friday.\nI'll  set it\nthe week after.")
    librarian.model.says(
        "The tile arrives Friday",
        statement("The tile arrives Friday.", kind="reported_status"),
        statement("I'll set it the week after.", kind="committed"),
        statement("The tile was paid for in full.", kind="reported_status"),
    )

    report = librarian.rebuild()

    assert quotes(librarian.call("statements", who=DAVE_ADDRESS)) == [
        "The tile arrives Friday.",
        "I'll set it the week after.",
    ]
    assert report.statements.dropped_quote_not_found == 1
    assert report.statements.kept == 2


def a_thread_of_three(librarian: Librarian) -> list[str]:
    """Dave asks, Argus answers, Dave confirms; plus an unrelated email from Maria."""
    mail, model = librarian.mail, librarian.model
    first = mail.receive("Could you send the tile order?", subject="Tile")
    second = mail.receive(
        "Sending the tile order tonight.", sender=ARGUS, to=DAVE, replying_to=first
    )
    third = mail.receive("Got the tile order, thanks.", replying_to=first)
    other = mail.receive("The countertop is templated.", sender=MARIA)
    model.says("Could you send the tile order", statement("Could you send the tile order?"))
    model.says("Sending the tile order tonight", statement("Sending the tile order tonight."))
    model.says("Got the tile order", statement("Got the tile order, thanks."))
    model.says("The countertop is templated", statement("The countertop is templated."))
    return [first, second, third, other]


def test_a_second_rebuild_with_nothing_changed_makes_no_model_calls(librarian: Librarian):
    a_thread_of_three(librarian)
    first = librarian.rebuild()
    calls = librarian.model.calls

    second = librarian.rebuild()

    assert librarian.model.calls == calls == 4
    assert second.model_calls_by_model() == {}
    assert (second.cache["extraction"].hits, second.cache["extraction"].misses) == (4, 0)
    assert first.model_calls_by_model() == {"claude-sonnet-5-5": 4}


def test_a_changed_prompt_re_extracts_every_episode(
    librarian: Librarian, monkeypatch: pytest.MonkeyPatch
):
    a_thread_of_three(librarian)
    first = librarian.rebuild()
    calls = librarian.model.calls

    monkeypatch.setattr(extraction, "PROMPT", extraction.PROMPT + "\nBe brief.\n")
    second = librarian.rebuild()

    assert librarian.model.calls - calls == 4
    assert second.extractor_version != first.extractor_version


def test_a_changed_episode_re_extracts_only_itself_and_the_later_episodes_in_its_thread(
    librarian: Librarian,
):
    _, second, _, _ = a_thread_of_three(librarian)
    librarian.rebuild()
    calls = librarian.model.calls

    librarian.mail.edit(second, "Sending the tile order tomorrow.")
    librarian.model.says("Sending the tile order tomorrow", statement("Sending the tile order"))
    report = librarian.rebuild()

    re_extracted = sorted(librarian.model.answered[calls:])
    assert re_extracted == ["Got the tile order", "Sending the tile order tomorrow"]
    assert (report.cache["extraction"].hits, report.cache["extraction"].misses) == (2, 2)


def test_a_reply_is_extracted_with_the_earlier_messages_of_its_thread(librarian: Librarian):
    a_thread_of_three(librarian)

    librarian.rebuild()

    (reply_prompt,) = [p for p in librarian.model.prompts if "Got the tile order" in p]
    assert "Could you send the tile order?" in reply_prompt
    assert "Sending the tile order tonight." in reply_prompt
    (unrelated,) = [p for p in librarian.model.prompts if "countertop" in p]
    assert "tile" not in unrelated


def test_ids_are_identical_across_rebuilds(librarian: Librarian):
    a_thread_of_three(librarian)
    librarian.rebuild()
    before = librarian.call("statements")

    librarian.rebuild()
    after = librarian.call("statements")

    assert after == before
    assert len(nodes(after, "Statement")) == 4


def test_a_failing_model_call_publishes_nothing_and_a_rerun_repeats_no_finished_work(
    librarian: Librarian,
):
    a_thread_of_three(librarian)
    published = librarian.rebuild()
    before = librarian.call("statements")
    librarian.mail.receive("The plumber comes Monday.", sender=RICK)
    librarian.mail.receive("The sink is back-ordered.", sender=RICK)
    librarian.model.says("The plumber comes Monday", statement("The plumber comes Monday."))
    librarian.model.says("The sink is back-ordered", statement("The sink is back-ordered."))
    librarian.model.failing.add("The plumber comes Monday")

    with pytest.raises(LibrarianError, match="nothing was published"):
        librarian.rebuild()

    assert current_build(librarian).name == published.build_id
    assert librarian.call("statements") == before
    done = set(librarian.model.answered)
    answered_so_far = len(librarian.model.answered)

    librarian.model.failing.clear()
    librarian.rebuild()

    rerun = librarian.model.answered[answered_so_far:]
    assert "The plumber comes Monday" in rerun
    assert not done & set(rerun)
    assert len(quotes(librarian.call("statements", who="rick@alvarezkb.example"))) == 2


def test_the_server_answers_from_a_newly_published_build_without_a_restart(
    librarian: Librarian,
):
    librarian.mail.receive("The tile arrives Friday.")
    librarian.model.says("The tile arrives Friday", statement("The tile arrives Friday."))
    librarian.rebuild()
    assert quotes(librarian.call("statements")) == ["The tile arrives Friday."]

    librarian.mail.receive("The grout is in too.")
    librarian.model.says("The grout is in too", statement("The grout is in too."))
    librarian.rebuild()

    assert quotes(librarian.call("statements")) == [
        "The tile arrives Friday.",
        "The grout is in too.",
    ]


def test_a_published_build_is_its_database_file_alone_read_only_with_its_report_beside_it(
    librarian: Librarian,
):
    a_thread_of_three(librarian)

    report = librarian.rebuild()

    build = current_build(librarian)
    assert build.name == report.build_id
    assert sorted(path.name for path in build.iterdir()) == ["graph.lbdb"]  # no write-ahead log
    assert is_read_only(build) and is_read_only(build / "graph.lbdb")
    beside = build.parent / f"{report.build_id}.report.json"
    assert BuildReport.model_validate_json(beside.read_text()) == report
    assert report.nodes == {"Person": 3, "Episode": 4, "Statement": 4, "Topic": 0}
    assert report.edges == {"SAID": 4, "RECORDED_IN": 4, "PARTICIPATED_IN": 8, "ABOUT": 0}


def test_only_the_last_few_builds_are_kept(librarian: Librarian):
    a_thread_of_three(librarian)

    reports = [librarian.rebuild() for _ in range(5)]

    builds = sorted(p.name for p in (librarian.data_root.graph / "builds").iterdir())
    kept = [r.build_id for r in reports[-3:]]
    assert builds == sorted([*kept, *(f"{build_id}.report.json" for build_id in kept)])


def test_before_the_first_build_the_server_says_to_run_the_librarian(librarian: Librarian):
    message = librarian.call_failing("statements")

    assert "pa librarian rebuild" in message


def two_davids(librarian: Librarian) -> None:
    librarian.mail.receive("The toast is yours if you want it.", sender=DAVE)
    librarian.mail.receive("Our pipes froze again.", sender="David Lee <dlee@example.org>")
    librarian.model.says("The toast is yours", statement("The toast is yours if you want it."))
    librarian.model.says("Our pipes froze", statement("Our pipes froze again."))
    librarian.rebuild()


def test_find_says_plainly_when_nothing_matches(librarian: Librarian):
    two_davids(librarian)

    result = librarian.call("find", name="Zebulon")

    assert result["matches"] == [] and result["nodes"] == []
    assert "Nothing matches 'Zebulon'" in result["message"]


def test_find_returns_the_candidates_for_an_ambiguous_name(librarian: Librarian):
    two_davids(librarian)

    result = librarian.call("find", name="david")

    names = {librarian_name(result, m["id"]) for m in result["matches"]}
    assert names == {"David Kim", "David Lee"}
    assert "2 candidates" in result["message"]


def test_find_matches_an_exact_address_name_or_alias_before_full_text(librarian: Librarian):
    two_davids(librarian)

    by_address = librarian.call("find", name="DKim@Example.net")
    by_name = librarian.call("find", name="david  kim")
    by_alias = librarian.call("find", name="gus")

    assert [
        (librarian_name(by_address, m["id"]), m["matched_on"]) for m in by_address["matches"]
    ] == [("David Kim", "address")]
    assert [(librarian_name(by_name, m["id"]), m["matched_on"]) for m in by_name["matches"]] == [
        ("David Kim", "name")
    ]
    assert [(librarian_name(by_alias, m["id"]), m["matched_on"]) for m in by_alias["matches"]] == [
        ("Argus McNevans", "alias")
    ]


def test_statements_by_an_ambiguous_name_return_the_candidates_instead_of_guessing(
    librarian: Librarian,
):
    two_davids(librarian)

    result = librarian.call("statements", who="David")

    assert quotes(result) == []
    assert len(result["matches"]) == 2
    assert "candidate's id" in result["message"]
    (lee,) = [m["id"] for m in result["matches"] if librarian_name(result, m["id"]) == "David Lee"]
    assert quotes(librarian.call("statements", who=lee)) == ["Our pipes froze again."]


def test_every_result_is_graph_shaped_with_episode_ids(librarian: Librarian):
    two_davids(librarian)

    found = librarian.call("find", name="David Lee")
    said = librarian.call("statements", who="dkim@example.net")

    for result in (found, said):
        assert result["nodes"]
        for item in [*result["nodes"], *result["edges"]]:
            assert item["id"] and item["type"] and item["episode_ids"]
    (statement_node,) = nodes(said, "Statement")
    (episode,) = nodes(said, "Episode")
    assert statement_node["episode_ids"] == [episode["id"]]
    assert statement_node["properties"]["l0_path"] == episode["properties"]["l0_path"]
    assert episode["properties"]["l0_path"].startswith("episodes/gmail/")
    node_ids = {node["id"] for node in said["nodes"]}
    assert {e["type"] for e in said["edges"]} == {"SAID", "RECORDED_IN"}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in said["edges"])


def test_statements_can_be_limited_to_a_span_of_time(librarian: Librarian):
    for day, text in ((3, "The tile is ordered."), (10, "The tile shipped."), (20, "Tile is in.")):
        librarian.mail.receive(text, day=day)
        librarian.model.says(text, statement(text))
    librarian.rebuild()

    result = librarian.call(
        "statements", who="dkim@example.net", since="2026-09-10", until="2026-09-19"
    )

    assert quotes(result) == ["The tile shipped."]


def test_pa_librarian_rebuild_runs_on_sonnet_at_medium_effort_and_prints_its_report(
    librarian: Librarian,
    extensions_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    librarian.mail.receive("The tile arrives Friday.")
    librarian.model.says("The tile arrives Friday", statement("The tile arrives Friday."))
    backends: list[tuple[str | None, str | None]] = []

    def claude(*, model: str | None = None, effort: str | None = None) -> FakeModelBackend:
        backends.append((model, effort))
        return librarian.model.backend

    monkeypatch.setattr(cli, "ClaudeCliBackend", claude)
    monkeypatch.setenv("PA_DATA_DIR", str(librarian.data_root.path))
    monkeypatch.setenv("PA_OWNER_NAME", OWNER.name)
    monkeypatch.setenv("PA_OWNER_EMAILS", ",".join(OWNER.email_addresses))
    monkeypatch.setenv("PA_LBUG_EXTENSIONS_DIR", str(extensions_root))

    assert cli.main(["librarian", "rebuild"]) == 0

    assert set(backends) == {("claude-sonnet-5-5", "medium")}
    out = capsys.readouterr().out
    assert "published build" in out and "1 to claude-sonnet-5-5" in out
    assert quotes(librarian.call("statements")) == ["The tile arrives Friday."]


def test_pa_mcp_serve_answers_over_stdio(librarian: Librarian, extensions_root: Path):
    librarian.mail.receive("The tile arrives Friday.")
    librarian.model.says("The tile arrives Friday", statement("The tile arrives Friday."))
    librarian.rebuild()
    server = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from pa_home.cli import main; raise SystemExit(main())", "mcp", "serve"],
        env={
            **os.environ,
            "PA_DATA_DIR": str(librarian.data_root.path),
            "PA_LBUG_EXTENSIONS_DIR": str(extensions_root),
        },
    )

    async def go() -> dict[str, Any] | None:
        # The initialize handshake, as Claude Code connects.
        async with Client(server, mode="legacy") as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {"find", "statements", "timeline"}
            return (await client.call_tool("find", {"name": "dkim@example.net"})).structured_content

    found = anyio.run(go)

    assert found is not None and found["message"] == "1 match for 'dkim@example.net'."


def test_timeline_returns_a_topics_statements_in_time_order_with_speakers_and_episodes(
    librarian: Librarian,
):
    mail, model = librarian.mail, librarian.model
    mail.receive("The countertop is templated.", sender=MARIA, day=9)
    mail.receive("Our pipes froze again.", day=5)
    mail.receive("The tile arrives Friday.", day=3)
    model.says(
        "The countertop is templated",
        statement("The countertop is templated.", topics=("Kitchen remodel",)),
    )
    model.says("Our pipes froze", statement("Our pipes froze again.", topics=("Frozen pipes",)))
    model.says(
        "The tile arrives Friday",
        statement("The tile arrives Friday.", topics=("Kitchen remodel",)),
    )
    model.places("Kitchen remodel", new="Kitchen remodel", description="Redoing the kitchen.")

    librarian.rebuild()
    result = librarian.call("timeline", topic="kitchen remodel")

    assert quotes(result) == ["The tile arrives Friday.", "The countertop is templated."]
    speakers = [
        librarian_name(result, speaker_of(result, s["id"])["id"])
        for s in nodes(result, "Statement")
    ]
    assert speakers == ["David Kim", "Maria Alvarez"]
    (topic,) = nodes(result, "Topic")
    assert topic["properties"]["name"] == "Kitchen remodel"
    assert topic["properties"]["description"] == "Redoing the kitchen."
    statement_ids = {s["id"] for s in nodes(result, "Statement")}
    about = [e for e in result["edges"] if e["type"] == "ABOUT"]
    assert {(e["source"], e["target"]) for e in about} == {(s, topic["id"]) for s in statement_ids}
    episode_ids = {e["id"] for e in nodes(result, "Episode")}
    assert len(episode_ids) == 2
    assert set(topic["episode_ids"]) == episode_ids
    for item in [*result["nodes"], *result["edges"]]:
        assert item["episode_ids"]
    assert "2 statements about Kitchen remodel" in result["message"]


def a_kitchen_remodel(librarian: Librarian) -> None:
    """Three emails about one remodel under three names: the first makes the topic, the second
    is placed under it by the model, the third names it by the alias the second left."""
    mail, model = librarian.mail, librarian.model
    mail.receive("The tile arrives Friday.", day=3)
    mail.receive("The cabinets ship Monday.", sender=MARIA, day=5)
    mail.receive("The kitchen cabinets are in.", sender=RICK, day=7)
    model.says(
        "The tile arrives", statement("The tile arrives Friday.", topics=("Kitchen remodel",))
    )
    model.says(
        "The cabinets ship", statement("The cabinets ship Monday.", topics=("kitchen cabinets",))
    )
    model.says(
        "The kitchen cabinets are in",
        statement("The kitchen cabinets are in.", topics=("Kitchen Cabinets",)),
    )
    model.places("Kitchen remodel", new="Kitchen remodel", description="Redoing the kitchen.")
    model.places("kitchen cabinets", under="Kitchen remodel")


def test_a_topic_name_is_placed_under_an_existing_topic_and_becomes_its_alias(
    librarian: Librarian,
):
    a_kitchen_remodel(librarian)

    report = librarian.rebuild()

    result = librarian.call("timeline", topic="kitchen cabinets")
    assert quotes(result) == [
        "The tile arrives Friday.",
        "The cabinets ship Monday.",
        "The kitchen cabinets are in.",
    ]
    (topic,) = nodes(result, "Topic")
    assert topic["properties"]["name"] == "Kitchen remodel"
    assert topic["properties"]["aliases"] == ["kitchen cabinets"]
    assert librarian.model.placed == [["Kitchen remodel"], ["kitchen cabinets"]]
    assert report.model_calls["topics"] == {"claude-sonnet-5-5": 2}
    assert report.nodes["Topic"] == 1 and report.edges["ABOUT"] == 3


def test_topic_decisions_are_reused_on_a_rebuild_with_nothing_changed(librarian: Librarian):
    a_kitchen_remodel(librarian)
    first = librarian.rebuild()
    before = librarian.call("timeline", topic="kitchen remodel")

    second = librarian.rebuild()

    assert librarian.call("timeline", topic="kitchen remodel") == before
    assert librarian.model.topic_calls == 2
    assert second.model_calls_by_model() == {}
    assert (second.cache["topics"].hits, second.cache["topics"].misses) == (2, 0)
    assert (first.cache["topics"].hits, first.cache["topics"].misses) == (0, 2)


def two_kitchen_topics(librarian: Librarian) -> None:
    mail, model = librarian.mail, librarian.model
    mail.receive("The tile arrives Friday.", day=3)
    mail.receive("The new fridge comes Tuesday.", sender=MARIA, day=4)
    mail.receive("Our pipes froze again.", day=5)
    model.says(
        "The tile arrives", statement("The tile arrives Friday.", topics=("Kitchen remodel",))
    )
    model.says(
        "The new fridge",
        statement("The new fridge comes Tuesday.", topics=("Kitchen appliances",)),
    )
    model.says("Our pipes froze", statement("Our pipes froze again.", topics=("Frozen pipes",)))
    librarian.rebuild()


def test_an_ambiguous_topic_name_returns_the_candidates_instead_of_a_guess(librarian: Librarian):
    two_kitchen_topics(librarian)

    result = librarian.call("timeline", topic="kitchen")

    assert quotes(result) == []
    candidates = {librarian_name(result, m["id"]) for m in result["matches"]}
    assert candidates == {"Kitchen remodel", "Kitchen appliances"}
    assert "candidate's id as `topic`" in result["message"]
    (appliances,) = [
        m["id"]
        for m in result["matches"]
        if librarian_name(result, m["id"]) == "Kitchen appliances"
    ]
    assert quotes(librarian.call("timeline", topic=appliances)) == ["The new fridge comes Tuesday."]


def test_a_topic_argument_never_resolves_to_a_person(librarian: Librarian):
    two_kitchen_topics(librarian)

    result = librarian.call("timeline", topic="Maria Alvarez")

    assert result["matches"] == [] and result["nodes"] == []
    assert "Nothing that `topic` can name matches 'Maria Alvarez'" in result["message"]


def test_statements_can_be_limited_to_one_topic_and_carry_their_topics(librarian: Librarian):
    two_kitchen_topics(librarian)

    about_the_remodel = librarian.call("statements", who=DAVE_ADDRESS, topic="kitchen remodel")
    by_dave = librarian.call("statements", who=DAVE_ADDRESS)

    assert quotes(about_the_remodel) == ["The tile arrives Friday."]
    assert "about Kitchen remodel" in about_the_remodel["message"]
    assert quotes(by_dave) == ["The tile arrives Friday.", "Our pipes froze again."]
    topics_by_quote = {
        s["properties"]["quote"]: librarian_name(by_dave, about["target"])
        for s in nodes(by_dave, "Statement")
        for about in by_dave["edges"]
        if about["type"] == "ABOUT" and about["source"] == s["id"]
    }
    assert topics_by_quote == {
        "The tile arrives Friday.": "Kitchen remodel",
        "Our pipes froze again.": "Frozen pipes",
    }


def test_find_covers_topics_by_name_and_by_full_text(librarian: Librarian):
    two_kitchen_topics(librarian)

    by_name = librarian.call("find", name="frozen PIPES")
    by_word = librarian.call("find", name="pipes")

    for result, matched_on in ((by_name, "name"), (by_word, "full_text")):
        (match,) = result["matches"]
        assert (match["type"], match["matched_on"]) == ("Topic", matched_on)
        assert librarian_name(result, match["id"]) == "Frozen pipes"
        (topic,) = result["nodes"]
        assert set(topic["properties"]) == {"name", "aliases", "description"}


def test_a_timeline_can_be_limited_to_a_span_of_time(librarian: Librarian):
    a_kitchen_remodel(librarian)
    librarian.rebuild()

    result = librarian.call(
        "timeline", topic="kitchen remodel", since="2026-09-04", until="2026-09-06"
    )

    assert quotes(result) == ["The cabinets ship Monday."]


def test_a_reply_is_placed_among_the_topics_its_thread_is_already_about(librarian: Librarian):
    mail, model = librarian.mail, librarian.model
    ask = mail.receive("Could you give the toast on Saturday?", subject="Saturday")
    mail.receive("Happy to give the toast.", sender=ARGUS, to=DAVE, replying_to=ask)
    model.says(
        "Could you give the toast",
        statement("Could you give the toast on Saturday?", topics=("Dave's wedding",)),
    )
    model.says("Happy to give", statement("Happy to give the toast.", topics=("The toast",)))
    model.places("The toast", under="Dave's wedding")

    librarian.rebuild()

    assert quotes(librarian.call("timeline", topic="the toast")) == [
        "Could you give the toast on Saturday?",
        "Happy to give the toast.",
    ]


def test_a_failing_topic_call_publishes_nothing_and_a_rerun_keeps_the_decisions_made(
    librarian: Librarian,
):
    a_kitchen_remodel(librarian)
    librarian.model.failing.add("kitchen cabinets")

    with pytest.raises(LibrarianError, match="nothing was published"):
        librarian.rebuild()

    assert not (librarian.data_root.graph / "current").exists()
    librarian.model.failing.clear()
    librarian.rebuild()
    assert librarian.model.placed == [["Kitchen remodel"], ["kitchen cabinets"]]
    assert len(quotes(librarian.call("timeline", topic="kitchen remodel"))) == 3


def test_a_topic_named_only_by_a_dropped_statement_is_never_made(librarian: Librarian):
    librarian.mail.receive("The tile arrives Friday.")
    librarian.model.says(
        "The tile arrives",
        statement("The tile arrives Friday.", topics=("Kitchen remodel",)),
        statement("Coy agreed to wire $500.", topics=("Wire transfer",)),
    )

    report = librarian.rebuild()

    assert librarian.model.placed == [["Kitchen remodel"]]
    assert report.nodes["Topic"] == 1
    assert librarian.call("find", name="wire transfer")["matches"] == []


def librarian_name(result: dict[str, Any], node_id: str) -> str:
    return next(n["properties"]["name"] for n in result["nodes"] if n["id"] == node_id)


def current_build(librarian: Librarian) -> Path:
    return (librarian.data_root.graph / "current").resolve(strict=True)


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


# Triage: mail is filtered by its Gmail category before extraction.


@pytest.mark.parametrize("category", ["Promotions", "Social"])
def test_promotions_and_social_mail_is_skipped_without_a_model_call(
    librarian: Librarian, category: str
):
    librarian.mail.receive(
        "50% off every sofa this weekend only!",
        sender="Sofa Barn <deals@sofabarn.example>",
        labels=f"Inbox,Category {category}",
    )
    librarian.mail.receive("The tile arrives Friday.")
    librarian.model.says("50% off every sofa", statement("50% off every sofa this weekend only!"))
    librarian.model.says("The tile arrives Friday", statement("The tile arrives Friday."))

    report = librarian.rebuild()

    assert [p for p in librarian.model.prompts if "sofa" in p] == []
    assert quotes(librarian.call("statements")) == ["The tile arrives Friday."]
    assert librarian.call("find", name="deals@sofabarn.example")["matches"] == []
    assert report.episodes.triage.skipped_by_category == 1
    assert report.episodes.extracted == 1


def updates_from_a_clinic_and_a_digest(librarian: Librarian, category: str = "Updates") -> None:
    mail, model = librarian.mail, librarian.model
    mail.receive(
        "Reminder: your appointment with Dr. Patel is Tuesday at 9:00.",
        sender="Lakeside Clinic <reminders@lakeside.example>",
        labels=f"Inbox,Category {category}",
    )
    mail.receive(
        "This week's digest: ten tips for spring cleaning.",
        sender="Home Digest <news@homedigest.example>",
        labels=f"Inbox,Category {category}",
    )
    model.judges("appointment with Dr. Patel", relevant=True)
    model.judges("ten tips for spring cleaning", relevant=False)
    model.says(
        "appointment with Dr. Patel",
        statement("your appointment with Dr. Patel is Tuesday at 9:00.", kind="reported_status"),
    )
    model.says("ten tips for spring cleaning", statement("ten tips for spring cleaning."))


@pytest.mark.parametrize("category", ["Updates", "Forums"])
def test_updates_and_forums_mail_is_extracted_only_when_the_relevance_check_says_it_matters(
    librarian: Librarian, category: str
):
    updates_from_a_clinic_and_a_digest(librarian, category)

    report = librarian.rebuild()

    assert sorted(librarian.model.judged) == [
        "appointment with Dr. Patel",
        "ten tips for spring cleaning",
    ]
    assert librarian.model.answered == ["appointment with Dr. Patel"]
    assert quotes(librarian.call("statements")) == [
        "your appointment with Dr. Patel is Tuesday at 9:00."
    ]
    assert librarian.call("find", name="news@homedigest.example")["matches"] == []
    assert (report.episodes.triage.relevant, report.episodes.triage.not_relevant) == (1, 1)
    assert (report.episodes.extracted, report.episodes.skipped) == (1, 1)
    assert report.model_calls["triage"] == {"claude-sonnet-5-5": 2}


def test_a_rebuild_reuses_each_relevance_answer(librarian: Librarian):
    updates_from_a_clinic_and_a_digest(librarian)
    librarian.rebuild()
    calls = librarian.model.calls

    report = librarian.rebuild()

    assert librarian.model.calls == calls
    assert (report.cache["triage"].hits, report.cache["triage"].misses) == (2, 0)
    assert (report.episodes.triage.relevant, report.episodes.triage.not_relevant) == (1, 1)
    assert len(quotes(librarian.call("statements"))) == 1


def test_primary_and_uncategorised_mail_is_extracted_without_a_relevance_check(
    librarian: Librarian,
):
    mail, model = librarian.mail, librarian.model
    for text, labels in (
        ("The tile arrives Friday.", None),
        ("The grout is in too.", "Inbox,Important,Category Personal"),
        ("Sending the deposit tonight.", "Sent"),
    ):
        mail.receive(text, labels=labels)
        model.says(text, statement(text))

    report = librarian.rebuild()

    assert librarian.model.judged == []
    assert "triage" not in report.model_calls
    assert len(quotes(librarian.call("statements"))) == 3
    assert report.episodes.triage.primary == 3


# Daily notes and chats: the owner's own episodes.


def test_a_daily_note_yields_the_owners_statements_with_the_owner_as_author(
    librarian: Librarian,
):
    librarian.mail.note(
        "## Kitchen\nOrdered the tile from Alvarez today.\nI'll call the plumber on Monday.\n",
        day=5,
    )
    librarian.model.says(
        "Ordered the tile from Alvarez",
        statement("Ordered the tile from Alvarez today.", kind="reported_status"),
        statement("I'll call the plumber on Monday.", kind="committed", speaker="Rick"),
    )

    report = librarian.rebuild()

    by_owner = librarian.call("statements", who="argus@example.com")
    assert sorted(quotes(by_owner)) == [
        "I'll call the plumber on Monday.",
        "Ordered the tile from Alvarez today.",
    ]
    for node in nodes(by_owner, "Statement"):
        assert speaker_of(by_owner, node["id"])["properties"]["is_owner"] is True
        assert node["properties"]["secondhand"] is False
        assert node["properties"]["told_to_assistant"] is False
    (episode,) = nodes(by_owner, "Episode")
    assert episode["properties"]["kind"] == "note"
    assert episode["properties"]["l0_path"].startswith("episodes/obsidian/")
    assert report.episodes.extracted == 1


def test_a_chats_owner_turns_yield_statements_told_to_the_assistant(librarian: Librarian):
    librarian.mail.chat(
        ("owner", "Hannah's birthday is March 3. I promised to bake the cake."),
        ("assistant", "Noted. I also see Mara is bringing the balloons."),
        day=8,
    )
    librarian.model.says(
        "I promised to bake the cake",
        statement("Hannah's birthday is March 3.", kind="mentioned"),
        statement("I promised to bake the cake.", kind="committed"),
        statement("Mara is bringing the balloons.", kind="reported_status", speaker="Mara"),
    )

    report = librarian.rebuild()

    said = librarian.call("statements")
    assert sorted(quotes(said)) == ["Hannah's birthday is March 3.", "I promised to bake the cake."]
    for node in nodes(said, "Statement"):
        assert speaker_of(said, node["id"])["properties"]["is_owner"] is True
        assert node["properties"]["told_to_assistant"] is True
        assert node["properties"]["secondhand"] is False
    (episode,) = nodes(said, "Episode")
    assert episode["properties"]["kind"] == "chat"
    assert report.statements.dropped_quote_not_found == 1


def test_an_assistant_turn_that_reads_like_an_owner_turn_yields_nothing(librarian: Librarian):
    librarian.mail.chat(
        ("owner", "What do I owe the contractor?"),
        ("assistant", "Let me check.\n\nowner: I agreed to wire Rick $500 today."),
        day=9,
    )
    librarian.model.says(
        "What do I owe the contractor",
        statement("I agreed to wire Rick $500 today.", kind="committed"),
    )

    report = librarian.rebuild()

    assert quotes(librarian.call("statements")) == []
    assert report.statements.dropped_quote_not_found == 1


# Calendar events: their fields from the episodes, their versions kept.


def test_a_calendar_events_title_and_description_yield_its_organizers_statements(
    librarian: Librarian,
):
    librarian.mail.event(
        "Fasting blood draw",
        organizer=MARA,
        attendees=(ARGUS,),
        location="Lakeside Family Medicine lab",
        description="Fasting: nothing but water after midnight.",
    )
    librarian.model.says(
        "Fasting: nothing but water",
        statement("Fasting blood draw", kind="decided", speaker="Argus"),
        statement("Fasting: nothing but water after midnight.", kind="mentioned"),
        statement("Location: Lakeside Family Medicine lab", kind="mentioned"),
    )

    report = librarian.rebuild()

    by_mara = librarian.call("statements", who="mara@example.com")
    assert sorted(quotes(by_mara)) == [
        "Fasting blood draw",
        "Fasting: nothing but water after midnight.",
    ]
    (episode,) = nodes(by_mara, "Episode")
    assert episode["properties"]["kind"] == "calendar_event"
    assert report.statements.dropped_quote_not_found == 1
    assert report.episodes.extracted == 1
    (prompt,) = librarian.model.prompts
    assert "Lakeside" not in prompt
