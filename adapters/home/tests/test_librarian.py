"""The librarian and the PA MCP server, end to end at one seam: fixture L0 and a scripted
model in, `rebuild`, then answers through an in-process MCP client and the build report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from pa_core.envelope import Envelope
from pa_core.librarian.ports import LibrarianModels, ModelChoice
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
