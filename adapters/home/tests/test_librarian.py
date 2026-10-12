"""The librarian and the PA MCP server, end to end at one seam: fixture L0 and a scripted
model in, `rebuild`, then answers through an in-process MCP client and the build report."""

from __future__ import annotations

import itertools
import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from pa_core.envelope import Envelope
from pa_core.librarian import extraction
from pa_core.librarian.ports import LibrarianModels, ModelChoice
from pa_core.librarian.rebuild import LibrarianError
from pa_core.librarian.report import BuildReport
from pa_core.model_client import FakeModelBackend, ModelBackendError, ModelRequest
from pa_core.normalizers.email import normalize_email
from pa_core.owner import Owner
from pa_home import cli
from pa_home.config import DataRoot
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.graph import extensions
from pa_home.graph.reader import Limits
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

    assert backends == [("claude-sonnet-5-5", "medium")]
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
            assert {tool.name for tool in tools.tools} == {"find", "statements", "query"}
            return (await client.call_tool("find", {"name": "dkim@example.net"})).structured_content

    found = anyio.run(go)

    assert found is not None and found["message"] == "1 match for 'dkim@example.net'."


def test_query_answers_a_read_with_rows_whose_nodes_and_edges_are_in_the_graph(
    librarian: Librarian,
):
    two_davids(librarian)

    result = librarian.call(
        "query",
        cypher="MATCH (p:Person)-[said:SAID]->(s:Statement) WHERE p.name = $name "
        "RETURN p, said, s, s.quote AS quote",
        params={"name": "David Lee"},
    )

    (row,) = result["rows"]
    assert result["columns"] == ["p", "said", "s", "quote"]
    assert row["quote"] == "Our pipes froze again."
    assert librarian_name(result, row["p"]) == "David Lee"
    (said,) = result["edges"]
    assert (said["id"], said["source"], said["target"]) == (row["said"], row["p"], row["s"])
    assert result["truncated"] is False
    assert result["message"] == "1 row."


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (:Person {id: 'p1', name: 'Mallory'})",
        "MATCH (p:Person) SET p.name = 'Mallory'",
        "MATCH (p:Person) DETACH DELETE p",
        "MERGE (:Person {id: 'p1'})",
        "MATCH (p:Person) REMOVE p.name",
        "DROP TABLE Person",
        "COPY (MATCH (p:Person) RETURN p.name) TO '/tmp/people.csv'",
        "COPY Person FROM '/tmp/people.csv'",
        "LOAD FROM '/tmp/people.csv' RETURN *",
        "LOAD EXTENSION '/tmp/libevil.lbug_extension'",
        "INSTALL httpfs",
        "UNINSTALL fts",
        "UPDATE fts",
        "ATTACH '/tmp/other.lbdb' AS other (dbtype lbug)",
        "EXPORT DATABASE '/tmp/dump'",
        "IMPORT DATABASE '/tmp/dump'",
        "CHECKPOINT",
        "CALL timeout=0",
        "CALL timeout=0; MATCH (p:Person) RETURN p",
        "MATCH (p:Person) RETURN p; MATCH (s:Statement) RETURN s",
        "match (p:Person) /* a comment */ set p.name = 'x'",
        "CALL CREATE_FTS_INDEX('Person', 'evil', ['name'])",
    ],
)
def test_query_rejects_anything_but_one_read_statement_before_it_reaches_the_database(
    librarian: Librarian, cypher: str
):
    # No build is published yet, so a statement that reached the database would fail with
    # "No claims graph has been published" instead.
    message = librarian.call_failing("query", cypher=cypher)

    assert "Rejected" in message
    assert "published" not in message


def test_query_never_writes_a_file(librarian: Librarian, tmp_path: Path):
    two_davids(librarian)
    target = tmp_path / "people.csv"

    message = librarian.call_failing(
        "query", cypher=f"COPY (MATCH (p:Person) RETURN p.name) TO '{target}'"
    )

    assert "Rejected" in message and "COPY" in message
    assert not target.exists()


def test_query_accepts_reads_that_mention_write_words_in_strings_comments_and_properties(
    librarian: Librarian,
):
    two_davids(librarian)

    result = librarian.call(
        "query",
        cypher="// set, create, or delete nothing\n"
        "MATCH (p:Person) WHERE p.name <> 'Create; Set; Delete' "
        "RETURN count(*) AS `delete people`;",
    )

    assert result["rows"] == [{"delete people": 3}]


def test_query_caps_its_rows_and_says_so(librarian: Librarian):
    two_davids(librarian)

    capped = librarian.call("query", cypher="UNWIND range(1, 5000) AS i RETURN i")
    whole = librarian.call("query", cypher="UNWIND range(1, 200) AS i RETURN i")

    assert [row["i"] for row in capped["rows"]] == list(range(1, 201))
    assert capped["truncated"] is True
    assert "Only the first 200 rows" in capped["message"]
    assert len(whole["rows"]) == 200 and whole["truncated"] is False


RUNAWAY = "MATCH p = (a:Person)-[*]-(b:Person) RETURN count(*)"
"""Every walk of up to 30 hops between two people: it outgrows any memory or time limit."""


def test_a_runaway_query_is_stopped_and_the_next_call_succeeds(librarian: Librarian):
    a_thread_of_three(librarian)
    two_davids(librarian)

    message = librarian.call_failing("query", cypher=RUNAWAY)

    assert "stopped" in message
    assert librarian.call("query", cypher="MATCH (p:Person) RETURN count(*) AS n")["rows"] == [
        {"n": 4}
    ]
    assert len(quotes(librarian.call("statements"))) == 6


def test_a_query_worker_over_its_memory_cap_is_killed_and_replaced(librarian: Librarian):
    a_thread_of_three(librarian)
    two_davids(librarian)
    # A buffer pool too big to fill and a long timeout leave only the memory cap to stop it.
    librarian.server = graph_server(
        librarian.data_root.graph,
        extensions_root=librarian.extensions_root,
        query_limits=Limits(
            timeout_seconds=60, memory_bytes=200 << 20, buffer_pool_bytes=4 << 30, max_rows=10
        ),
    )

    message = librarian.call_failing("query", cypher=RUNAWAY)

    assert "stopped" in message and "200 MB of memory" in message
    assert librarian.call("query", cypher="MATCH (p:Person) RETURN count(*) AS n")["rows"] == [
        {"n": 4}
    ]
    assert len(quotes(librarian.call("statements"))) == 6


def test_query_unpacks_paths_into_the_graph(librarian: Librarian):
    a_thread_of_three(librarian)
    librarian.rebuild()

    result = librarian.call(
        "query",
        cypher="MATCH p = (a:Person)-[* SHORTEST 1..4]-(b:Person) "
        "WHERE a.name = 'Maria Alvarez' AND b.name = 'David Kim' "
        "RETURN p, nodes(p) AS stops, length(p) AS hops",
    )

    (row,) = result["rows"]
    assert row["hops"] == 4  # Maria -> her email -> Argus -> a tile email -> Dave
    path = row["p"]
    assert path["nodes"] == row["stops"]
    assert [librarian_name(result, path["nodes"][i]) for i in (0, 2, 4)] == [
        "Maria Alvarez",
        "Argus McNevans",
        "David Kim",
    ]
    assert {node["type"] for node in result["nodes"]} == {"Person", "Episode"}
    assert sorted(node["id"] for node in result["nodes"]) == sorted(path["nodes"])
    assert [edge["id"] for edge in result["edges"]] == path["edges"]
    for edge, (here, there) in zip(result["edges"], itertools.pairwise(path["nodes"]), strict=True):
        assert {edge["source"], edge["target"]} == {here, there}
        assert edge["type"] == "PARTICIPATED_IN"
    maria = next(n for n in result["nodes"] if n["id"] == path["nodes"][0])
    assert set(maria["properties"]) == {"name", "aliases", "addresses", "is_owner"}


def librarian_name(result: dict[str, Any], node_id: str) -> str:
    return next(n["properties"]["name"] for n in result["nodes"] if n["id"] == node_id)


def current_build(librarian: Librarian) -> Path:
    return (librarian.data_root.graph / "current").resolve(strict=True)


def is_read_only(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o222 == 0
