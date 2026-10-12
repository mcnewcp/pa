"""The librarian and the PA MCP server, end to end at one seam: fixture L0 and a scripted
model in, `rebuild`, then answers through an in-process MCP client and the build report."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from pa_core.envelope import Envelope
from pa_core.librarian import extraction
from pa_core.librarian.ports import LibrarianModels, ModelChoice
from pa_core.librarian.rebuild import LibrarianError
from pa_core.librarian.report import BuildReport
from pa_core.model_client import FakeModelBackend, ModelBackendError, ModelRequest
from pa_core.normalizers.email import normalize_email
from pa_core.owner import Owner
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
    ) -> str:
        """Store an email and return its episode id (also usable as its thread's id)."""
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
        raw = (
            "\r\n".join([*headers, 'Content-Type: text/plain; charset="utf-8"', "", body]) + "\r\n"
        ).encode()
        envelope = normalize_email(raw, captured_at=datetime(2026, 10, 1, tzinfo=UTC))
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


def statement(quote: str, kind: str = "mentioned", speaker: str = "", summary: str = "") -> dict:
    return {
        "speaker": speaker or "the sender",
        "kind": kind,
        "summary": summary or f"Said: {quote}",
        "quote": quote,
    }


@dataclass
class ScriptedModel:
    """Replies to an extraction prompt by the scripted phrase found last in it: the episode
    comes after its thread's earlier messages, so its own phrase is the last one."""

    replies: dict[str, list[dict]] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    answered: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.backend = FakeModelBackend.replying(self._reply)

    def says(self, phrase: str, *statements: dict) -> None:
        self.replies[phrase] = list(statements)

    @property
    def calls(self) -> int:
        return len(self.backend.requests)

    @property
    def prompts(self) -> list[str]:
        return [request.prompt for request in self.backend.requests]

    def _reply(self, request: ModelRequest) -> dict[str, Any]:
        present = [phrase for phrase in self.replies if phrase in request.prompt]
        if not present:
            return {"statements": []}
        phrase = max(present, key=request.prompt.rfind)
        if phrase in self.failing:
            raise ModelBackendError(f"scripted failure for {phrase!r}")
        self.answered.append(phrase)
        return {"statements": self.replies[phrase]}


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
def librarian(tmp_path: Path, extensions_root: Path) -> Librarian:
    return Librarian(tmp_path / "pa-data", extensions_root)


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
    assert report.nodes == {"Person": 3, "Episode": 4, "Statement": 4}
    assert report.edges == {"SAID": 4, "RECORDED_IN": 4, "PARTICIPATED_IN": 8}


def test_only_the_last_few_builds_are_kept(librarian: Librarian):
    a_thread_of_three(librarian)

    reports = [librarian.rebuild() for _ in range(5)]

    builds = sorted(p.name for p in (librarian.data_root.graph / "builds").iterdir())
    kept = [r.build_id for r in reports[-3:]]
    assert builds == sorted([*kept, *(f"{build_id}.report.json" for build_id in kept)])


def test_before_the_first_build_the_server_says_to_run_the_librarian(librarian: Librarian):
    message = librarian.call_failing("statements")

    assert "pa librarian rebuild" in message


def current_build(librarian: Librarian) -> Path:
    return (librarian.data_root.graph / "current").resolve(strict=True)


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
