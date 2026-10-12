"""The PA MCP server's tools, engine-neutral: name resolution and plain messages around the
domain queries of a `ClaimsGraph`."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from pydantic import Field

from pa_core.claims.queries import ClaimsGraph, Found, Graph, Match
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


class ClaimsTools:
    def __init__(self, graph: ClaimsGraph) -> None:
        self._graph = graph

    def find(self, name: str) -> ToolResult:
        found = self._graph.find(name.strip())
        return _found_result(name, found)

    def statements(
        self, who: str | None = None, since: str | None = None, until: str | None = None
    ) -> ToolResult:
        since_at = _parse_moment(since, "since", end_of_day=False)
        until_at = _parse_moment(until, "until", end_of_day=True)
        speaker: str | None = None
        speaker_name = "anyone"
        if who is not None and who.strip():
            found = self._graph.find(who.strip())
            if len(found.matches) != 1:
                return _unresolved(who, found)
            speaker = found.matches[0].id
            speaker_name = _display_name(found.graph, speaker)
        graph = self._graph.statements(speaker, since_at, until_at)
        count = sum(1 for node in graph.nodes if node.type == "Statement")
        span = _span(since, until)
        if count == 0:
            message = f"No statements by {speaker_name}{span}."
        else:
            plural = "s" if count != 1 else ""
            message = f"{count} statement{plural} by {speaker_name}{span}, oldest first."
        return ToolResult(nodes=graph.nodes, edges=graph.edges, message=message)


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


def _unresolved(who: str, found: Found) -> ToolResult:
    result = _found_result(who, found)
    if found.matches:
        result.message += " Call again with one candidate's id as `who`."
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
