"""The PA MCP server's tools, engine-neutral: name resolution and plain messages around the
domain queries of a `ClaimsGraph`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from pydantic import Field

from pa_core.claims.queries import ClaimsGraph, Direction, Found, Graph, Match, Rows, Schema
from pa_core.errors import PaError


class ToolInputError(PaError):
    """A tool argument the tool can't use, such as a malformed date."""


class ToolResult(Graph):
    """Every tool's result: a graph, a plain message, and any name matches."""

    message: str = Field(description="What the result holds, or why it is empty, in words.")
    matches: list[Match] = Field(
        default=[],
        description=(
            "For find, the matches, best first. For other tools, the candidates when a name "
            "matched more than one node: call again with one candidate's id."
        ),
    )


class QueryResult(Graph):
    """The `query` tool's result: the rows, the nodes and edges in them, and a message."""

    columns: list[str]
    rows: list[dict[str, Any]] = Field(description=Rows.model_fields["rows"].description)
    truncated: bool = Field(description=Rows.model_fields["truncated"].description)
    message: str = Field(description="How many rows came back, and whether more were cut off.")


class ClaimsTools:
    def __init__(self, graph: ClaimsGraph) -> None:
        self._graph = graph

    def find(self, name: str) -> ToolResult:
        found = self._graph.find(name.strip())
        return _found_result(name, found)

    def statements(
        self,
        who: str | None = None,
        topic: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> ToolResult:
        since_at = _parse_moment(since, "since", end_of_day=False)
        until_at = _parse_moment(until, "until", end_of_day=True)
        speaker = self._resolve(who, "who", SPEAKER_TYPES)
        if isinstance(speaker, ToolResult):
            return speaker
        about = self._resolve(topic, "topic", TOPIC_TYPES)
        if isinstance(about, ToolResult):
            return about
        graph = self._graph.statements(
            speaker.id if speaker else None, about.id if about else None, since_at, until_at
        )
        count = _count(graph, "Statement")
        by = f" by {speaker.name}" if speaker else " by anyone"
        span = f"{by}{f' about {about.name}' if about else ''}{_span(since, until)}"
        if count == 0:
            message = f"No statements{span}."
        else:
            message = f"{_plural(count, 'statement')}{span}, oldest first."
        return ToolResult(nodes=graph.nodes, edges=graph.edges, message=message)

    def timeline(
        self, topic: str, since: str | None = None, until: str | None = None
    ) -> ToolResult:
        since_at = _parse_moment(since, "since", end_of_day=False)
        until_at = _parse_moment(until, "until", end_of_day=True)
        about = self._resolve(topic, "topic", TOPIC_TYPES)
        if isinstance(about, ToolResult):
            return about
        if about is None:
            raise ToolInputError("timeline needs a topic: its id, or a name to find it by")
        graph = self._graph.timeline(about.id, since_at, until_at)
        count = _count(graph, "Statement")
        span = f" about {about.name}{_span(since, until)}"
        if count == 0:
            message = f"Nothing recorded{span}."
        else:
            message = f"{_plural(count, 'statement')}{span}, oldest first."
        return ToolResult(nodes=graph.nodes, edges=graph.edges, message=message)

    def open_loops(self, who: str | None = None, direction: str | None = None) -> ToolResult:
        if direction is not None and direction not in _DIRECTIONS:
            raise ToolInputError(
                f"direction must be 'owed_by' (what `who` owes) or 'owed_to' (what is owed to "
                f"`who`), or left out for both; got {direction!r}"
            )
        party = self._resolve(who, "who", SPEAKER_TYPES)
        if isinstance(party, ToolResult):
            return party
        graph = self._graph.open_loops(party.id if party else None, _DIRECTIONS.get(direction))
        # The graph also holds the closed requests and promises open ones came from.
        requests, commitments = (
            sum(1 for n in graph.nodes if n.type == t and n.properties.get("status") == "open")
            for t in ("Request", "Commitment")
        )
        if party is None:
            whose = ""
        elif direction == "owed_by":
            whose = f" owed by {party.name}"
        elif direction == "owed_to":
            whose = f" owed to {party.name}"
        else:
            whose = f" owed by or to {party.name}"
        if requests + commitments == 0:
            message = f"No open requests or commitments{whose}."
        else:
            message = (
                f"{_plural(requests, 'open request')} and "
                f"{_plural(commitments, 'open commitment')}{whose}, oldest first. Nothing is "
                "marked overdue: compare each due date with now."
            )
        return ToolResult(nodes=graph.nodes, edges=graph.edges, message=message)

    def schema(self) -> Schema:
        return self._graph.schema()

    def query(self, text: str, parameters: Mapping[str, Any] | None = None) -> QueryResult:
        rows = self._graph.query(text, parameters or {})
        count = len(rows.rows)
        plural = "s" if count != 1 else ""
        if rows.truncated:
            message = (
                f"Only the first {count} row{plural}: there were more, and they were cut off. "
                "Narrow the query, aggregate, or page with SKIP and LIMIT."
            )
        else:
            message = f"{count} row{plural}."
        return QueryResult(
            nodes=rows.graph.nodes,
            edges=rows.graph.edges,
            columns=rows.columns,
            rows=rows.rows,
            truncated=rows.truncated,
            message=message,
        )

    def _resolve(
        self, name: str | None, argument: str, types: frozenset[str]
    ) -> _Resolved | ToolResult | None:
        """The one node of `types` that `name` finds; None when no name is given; or, unless
        exactly one matches, a result holding the candidates (or saying there are none)."""
        if name is None or not name.strip():
            return None
        found = _only(self._graph.find(name.strip()), types)
        if len(found.matches) != 1:
            return _unresolved(name, found, argument)
        node_id = found.matches[0].id
        return _Resolved(node_id, _display_name(found.graph, node_id))


SPEAKER_TYPES = frozenset({"Person", "Organization"})
"""What a `who` argument may name."""
TOPIC_TYPES = frozenset({"Topic"})
"""What a `topic` argument may name."""
_DIRECTIONS: dict[str | None, Direction] = {"owed_by": "owed_by", "owed_to": "owed_to"}


@dataclass(frozen=True)
class _Resolved:
    id: str
    name: str
    """For messages: its name and id."""


def _only(found: Found, types: frozenset[str]) -> Found:
    """`found` without the matches (and their nodes) of other types."""
    matches = [match for match in found.matches if match.type in types]
    kept = {match.id for match in matches}
    nodes = [node for node in found.graph.nodes if node.id in kept]
    return Found(matches=matches, graph=Graph(nodes=nodes))


def _count(graph: Graph, node_type: str) -> int:
    return sum(1 for node in graph.nodes if node.type == node_type)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _found_result(name: str, found: Found) -> ToolResult:
    count = len(found.matches)
    if count == 0:
        message = (
            f"Nothing matches {name!r}: no id, address, name, or alias is exactly that, and "
            "full-text search found nothing. That is not proof there is no record: check the "
            "spelling, or try another name, a nickname, or an email address."
        )
    elif count == 1:
        message = f"1 match for {name!r}."
    else:
        message = f"{count} candidates match {name!r}, best first."
    return ToolResult(
        nodes=found.graph.nodes, edges=found.graph.edges, matches=found.matches, message=message
    )


def _unresolved(name: str, found: Found, argument: str) -> ToolResult:
    result = _found_result(name, found)
    if found.matches:
        result.message += f" Call again with one candidate's id as `{argument}`."
    else:
        result.message = (
            f"Nothing that `{argument}` can name matches {name!r}. That is not proof there "
            "is no record: check the spelling, try another name, or use find."
        )
    return result


def _display_name(graph: Graph, node_id: str) -> str:
    node = graph.node(node_id)
    name = node.properties.get("name") if node else None
    return f"{name} ({node_id})" if name else node_id


def _span(since: str | None, until: str | None) -> str:
    parts = []
    if since:
        parts.append(f" from {since}")
    if until:
        parts.append(f" until {until}")
    return "".join(parts)


def _parse_moment(value: str | None, name: str, *, end_of_day: bool) -> datetime | None:
    """An ISO 8601 date or datetime as an aware datetime; a datetime without an offset is UTC.

    A date alone is the start of that day in UTC; for an end (`until`), the start of the next
    day, so the date's whole day is included.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if end_of_day:
                day += timedelta(days=1)
            return datetime.combine(day, time(), tzinfo=UTC)
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ToolInputError(
            f"{name} must be an ISO 8601 date (2026-10-01) or datetime "
            f"(2026-10-01T09:00:00-05:00); got {value!r}"
        ) from error
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
