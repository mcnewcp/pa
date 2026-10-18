"""The PA MCP server's tools, engine-neutral: name resolution and plain messages around the
domain queries of a `ClaimsGraph`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, get_args

from pydantic import Field

from pa_core.claims.queries import ClaimsGraph, Direction, Found, Graph, Match, Rows, Schema
from pa_core.claims.results import BUDGET, CompactGraph, fit
from pa_core.errors import PaError


class ToolInputError(PaError):
    """A tool argument the tool can't use, such as a malformed date."""


class ToolResult(CompactGraph):
    """The result of `find` and every domain tool: a graph in the compact encoding
    (`pa_core.claims.results`), a plain message, and any name matches."""

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
    """The tools over `graph`. A date or time without a UTC offset is read in `time_zone`, the
    instance's (the owner's) time zone, so a date alone is that whole local day."""

    def __init__(self, graph: ClaimsGraph, time_zone: tzinfo) -> None:
        self._graph = graph
        self._zone = time_zone

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
        asked = self._criteria(who, topic, since, until)
        if isinstance(asked, ToolResult):
            return asked
        graph = self._graph.statements(asked.who_id, asked.topic_id, asked.since, asked.until)
        count = _count(graph, "Statement")
        by = f" by {asked.who.name}" if asked.who else " by anyone"
        span = f"{by}{asked.about}{asked.span}"
        if count == 0:
            message = f"No statements{span}."
        else:
            message = f"{_plural(count, 'statement')}{span}, oldest first."
        return _result(
            graph,
            message,
            asked.subjects,
            topic=asked.topic_id,
            anchors=_ids(graph, "Statement"),
            cut=_resume(graph, asked, "statements"),
        )

    def timeline(
        self, topic: str, since: str | None = None, until: str | None = None
    ) -> ToolResult:
        asked = self._criteria(None, topic, since, until)
        if isinstance(asked, ToolResult):
            return asked
        if asked.topic is None:
            raise ToolInputError("timeline needs a topic: its id, or a name to find it by")
        graph = self._graph.timeline(asked.topic.id, asked.since, asked.until)
        counted = [
            _plural(count, noun)
            for count, noun in (
                (_count(graph, "Statement"), "statement"),
                (_count(graph, "CalendarEvent"), "calendar event"),
                (_count(graph, "Request"), "request"),
                (_count(graph, "Commitment"), "commitment"),
            )
            if count
        ]
        span = f"{asked.about}{asked.span}"
        if not counted:
            message = f"Nothing recorded{span}."
        else:
            listed = " and ".join(
                [", ".join(counted[:-1]), counted[-1]] if counted[1:] else counted
            )
            message = f"{listed}{span}, oldest first."
        return _result(
            graph,
            message,
            asked.subjects,
            topic=asked.topic_id,
            anchors=_ids(graph, "Statement", "CalendarEvent"),
            cut=_resume(graph, asked, "statements and calendar events"),
        )

    def events(
        self,
        who: str | None = None,
        topic: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> ToolResult:
        asked = self._criteria(who, topic, since, until)
        if isinstance(asked, ToolResult):
            return asked
        graph = self._graph.events(asked.who_id, asked.topic_id, asked.since, asked.until)
        events = [node for node in graph.nodes if node.type == "CalendarEvent"]
        with_who = f" with {asked.who.name}" if asked.who else ""
        span = f"{with_who}{asked.about}{asked.span}"
        if not events:
            message = f"No calendar events{span}."
        else:
            message = f"{_plural(len(events), 'calendar event')}{span}, by start time."
            cancelled = sum(1 for node in events if node.properties.get("status") == "cancelled")
            if cancelled:
                message += f" {cancelled} of them cancelled."
        return _result(
            graph,
            message,
            asked.subjects,
            topic=asked.topic_id,
            anchors=_ids(graph, "CalendarEvent"),
            cut=_resume(graph, asked, "calendar events"),
        )

    def open_loops(self, who: str | None = None, direction: Direction | None = None) -> ToolResult:
        if direction is not None and direction not in _DIRECTIONS:
            raise ToolInputError(
                f"direction must be 'owed_by' (what `who` owes) or 'owed_to' (what is owed to "
                f"`who`), or left out for both; got {direction!r}"
            )
        party = self._resolve(who, "who", SPEAKER_TYPES)
        if isinstance(party, ToolResult):
            return party
        graph = self._graph.open_loops(party.id if party else None, direction)
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
        open_loops = [
            node.id
            for node in graph.nodes
            if node.type in ("Request", "Commitment") and node.properties.get("status") == "open"
        ]
        return _result(
            graph,
            message,
            [party.id] if party else [],
            anchors=open_loops,
            cut=lambda kept, _: (
                f"{_TOO_MUCH} only the first {kept} open requests and commitments, oldest first. "
                "Narrow it with `who` and `direction`."
            ),
        )

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

    def _criteria(
        self, who: str | None, topic: str | None, since: str | None, until: str | None
    ) -> _Criteria | ToolResult:
        """What a domain query is asked for: `who` and `topic` resolved, and the span parsed;
        or, if either name doesn't resolve to exactly one node, the result saying so."""
        since_at = _parse_moment(since, "since", self._zone, end_of_day=False)
        until_at = _parse_moment(until, "until", self._zone, end_of_day=True)
        party = self._resolve(who, "who", SPEAKER_TYPES)
        if isinstance(party, ToolResult):
            return party
        about = self._resolve(topic, "topic", TOPIC_TYPES)
        if isinstance(about, ToolResult):
            return about
        return _Criteria(party, about, since_at, until_at, _span(since, until))

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
_DIRECTIONS: tuple[Direction, ...] = get_args(Direction.__value__)


@dataclass(frozen=True)
class _Resolved:
    id: str
    name: str
    """For messages: its name and id."""


@dataclass(frozen=True)
class _Criteria:
    """A domain query's `who`, `topic`, and span, as `_criteria` resolves and parses them."""

    who: _Resolved | None
    topic: _Resolved | None
    since: datetime | None
    until: datetime | None
    span: str
    """For messages: the span as given."""

    @property
    def who_id(self) -> str | None:
        return self.who.id if self.who else None

    @property
    def topic_id(self) -> str | None:
        return self.topic.id if self.topic else None

    @property
    def subjects(self) -> list[str]:
        """The ids of the nodes the call names."""
        return [resolved.id for resolved in (self.who, self.topic) if resolved]

    @property
    def about(self) -> str:
        """For messages: the topic, if one was given."""
        return f" about {self.topic.name}" if self.topic else ""


def _only(found: Found, types: frozenset[str]) -> Found:
    """`found` without the matches (and their nodes) of other types."""
    matches = [match for match in found.matches if match.type in types]
    kept = {match.id for match in matches}
    nodes = [node for node in found.graph.nodes if node.id in kept]
    return Found(matches=matches, graph=Graph(nodes=nodes))


type _Cut = Callable[[int, str], str]
"""What a result cut to its first `kept` items adds to its message, given the id of the first
item left out: that it was cut, and how to get the rest."""

_TOO_MUCH = " That is more than one result can hold, so here are"
_NOTE_ROOM = 400
"""Room kept in the budget for a cut result's note, and a message's last words."""


def _result(
    graph: Graph,
    message: str,
    subjects: list[str],
    *,
    anchors: list[str],
    cut: _Cut,
    topic: str | None = None,
    matches: list[Match] | None = None,
) -> ToolResult:
    """`graph` as a tool result in the compact encoding, within the budget: if all of it
    doesn't fit, the items of as many of `anchors` as fit, in order, with `cut`'s note.
    `subjects` are the ids of the nodes the call is about, and `topic` the topic it is limited
    to."""
    matches = matches or []
    room = BUDGET - len(ToolResult(message=message).model_dump_json())
    room -= _NOTE_ROOM
    beside = {match.id: len(match.model_dump_json()) + 1 for match in matches}
    while True:
        encoded, kept = fit(graph, anchors, room, subjects, topic, beside)
        note = cut(kept, anchors[kept]) if kept < len(anchors) else ""
        present = {node.id for node in encoded.nodes}
        result = ToolResult(
            nodes=encoded.nodes,
            edges=encoded.edges,
            message=message + note,
            matches=[match for match in matches if match.id in present],
        )
        over = len(result.model_dump_json()) + _NOTE_ROOM - BUDGET
        if over <= 0 or kept == 0:
            return result
        room -= over


def _ids(graph: Graph, *node_types: str) -> list[str]:
    return [node.id for node in graph.nodes if node.type in node_types]


def _resume(graph: Graph, asked: _Criteria, items: str) -> _Cut:
    """The note for a time-ordered result cut short: call again from the first item left
    out, a statement (`at`) or a calendar event (`starts_at`)."""

    def note(kept: int, left_out: str) -> str:
        node = graph.node(left_out)
        properties = node.properties if node else {}
        start = properties.get("at") or properties.get("starts_at")
        until = " and the same `until`" if asked.until else ""
        return (
            f"{_TOO_MUCH} only the first {kept} {items}, oldest first. For the rest, call "
            f"again with since={start}{until}."
        )

    return note


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
    subjects = [match.id for match in found.matches]
    return _result(
        found.graph,
        message,
        subjects,
        anchors=subjects,
        matches=found.matches,
        cut=lambda kept, _: (
            f"{_TOO_MUCH} only the first {kept} matches, best first. Give a fuller name, an "
            "email address, or an id."
        ),
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


def _parse_moment(
    value: str | None, name: str, zone: tzinfo, *, end_of_day: bool
) -> datetime | None:
    """An ISO 8601 date or datetime as an aware datetime; one without an offset is in `zone`.

    A date alone is the start of that day in `zone`; for an end (`until`), the start of the
    next day, so the date's whole local day is included.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            if end_of_day:
                day += timedelta(days=1)
            return datetime.combine(day, time(), tzinfo=zone)
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ToolInputError(
            f"{name} must be an ISO 8601 date (2026-10-01) or datetime "
            f"(2026-10-01T09:00:00-05:00); got {value!r}"
        ) from error
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)
