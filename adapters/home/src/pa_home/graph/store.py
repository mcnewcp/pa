"""Building and publishing the claims graph in LadybugDB.

A build is a fresh database in a new build directory. It is loaded from the librarian's
records, indexed, checked against the records' counts, checkpointed, and closed. It is
published only if its directory then holds the database file alone (a leftover write-ahead
log would be replayed by every reader), made read-only first, and swapped in as `current`.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ladybug as lb

from pa_core.claims.records import EdgeRecord, GraphRecords, NodeRecord
from pa_core.claims.schema import EDGE_TYPES, NODE_TYPES, EdgeType, NodeType, PropertyType
from pa_core.errors import PaError
from pa_core.librarian.ports import PublishedBuild
from pa_home.graph.layout import DATABASE_FILE, GraphDirectory

KEEP_BUILDS = 3
"""Published builds kept, newest first; a reader part way through a call keeps working."""

SEARCH_TEXT = "search_text"
"""A node's flattened text for full-text search, which indexes `STRING` properties only."""


class FullTextIndex:
    def __init__(self, label: str, name: str, fields: tuple[str, ...]) -> None:
        self.label, self.name, self.fields = label, name, fields


FULL_TEXT_INDEXES = (
    FullTextIndex("Person", "person_fts", ("name", "aliases", "addresses")),
    FullTextIndex("Organization", "organization_fts", ("name", "aliases", "domains", "addresses")),
    FullTextIndex("Topic", "topic_fts", ("name", "aliases", "description")),
    FullTextIndex("Statement", "statement_fts", ("summary", "quote")),
)

_TYPES = {
    PropertyType.STRING: "STRING",
    PropertyType.STRINGS: "STRING[]",
    PropertyType.BOOL: "BOOLEAN",
    PropertyType.TIMESTAMP: "TIMESTAMP",
}
_CHUNK = 2000


class BuildError(PaError):
    """A build that failed a check; it was not published."""


class LadybugBuilder:
    def __init__(
        self,
        graph: GraphDirectory,
        *,
        fts_extension: Path,
        keep: int = KEEP_BUILDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._graph = graph
        self._fts_extension = fts_extension
        self._keep = keep
        self._clock = clock

    def build(self, records: GraphRecords) -> PublishedBuild:
        build_id = self._new_build_id()
        directory = self._graph.build(build_id)
        directory.mkdir(parents=True)
        try:
            node_counts, edge_counts = self._write(directory / DATABASE_FILE, records)
            _seal(directory)
        except BaseException:
            _remove_build(directory)
            raise
        self._publish(build_id)
        self._prune()
        return PublishedBuild(build_id, node_counts, edge_counts)

    def _new_build_id(self) -> str:
        build_id = self._clock().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        while self._graph.build(build_id).exists():
            build_id += "x"
        return build_id

    def _write(self, path: Path, records: GraphRecords) -> tuple[dict[str, int], dict[str, int]]:
        db = lb.Database(str(path))
        conn = lb.Connection(db)
        try:
            conn.execute(f"LOAD EXTENSION '{_literal(str(self._fts_extension))}'")
            for node_type in NODE_TYPES:
                conn.execute(_node_table(node_type))
            for edge_type in EDGE_TYPES:
                conn.execute(_edge_table(edge_type))
            _load_nodes(conn, records.nodes)
            _load_edges(conn, records.edges, records.labels_by_id())
            for index in FULL_TEXT_INDEXES:
                conn.execute(
                    f"CALL CREATE_FTS_INDEX('{index.label}', '{index.name}', ['{SEARCH_TEXT}'])"
                )
            counts = _check_counts(conn, records)
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
            db.close()
        return counts

    def _publish(self, build_id: str) -> None:
        pointer = self._graph.path / f".current.{build_id}"
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(Path("builds") / build_id)
        pointer.replace(self._graph.current)
        _fsync_dir(self._graph.path)

    def _prune(self) -> None:
        current = self._graph.current_build()
        builds = sorted(p for p in self._graph.builds.iterdir() if p.is_dir())
        for directory in builds[: -self._keep]:
            if directory != current:
                _remove_build(directory)
                self._graph.report(directory.name).unlink(missing_ok=True)


def _node_table(node_type: NodeType) -> str:
    columns = ["id STRING PRIMARY KEY", "episode_ids STRING[]"]
    columns += [f"{name} {_TYPES[kind]}" for name, kind in node_type.properties.items()]
    if any(index.label == node_type.label for index in FULL_TEXT_INDEXES):
        columns.append(f"{SEARCH_TEXT} STRING")
    return f"CREATE NODE TABLE {node_type.label}({', '.join(columns)})"


def _edge_table(edge_type: EdgeType) -> str:
    parts = [f"FROM {source} TO {target}" for source, target in edge_type.endpoints]
    parts += ["id STRING", "episode_ids STRING[]"]
    parts += [f"{name} {_TYPES[kind]}" for name, kind in edge_type.properties.items()]
    return f"CREATE REL TABLE {edge_type.label}({', '.join(parts)})"


def _assignments(properties: Mapping[str, PropertyType], extra: Iterable[str] = ()) -> str:
    """`id: r.id, …`, each property cast to its type so an all-null column still binds."""
    items = ["id: r.id", "episode_ids: CAST(r.episode_ids AS STRING[])"]
    items += [f"{name}: CAST(r.{name} AS {_TYPES[kind]})" for name, kind in properties.items()]
    items += [f"{name}: r.{name}" for name in extra]
    return ", ".join(items)


def _load_nodes(conn: lb.Connection, nodes: list[NodeRecord]) -> None:
    by_label: defaultdict[str, list[NodeRecord]] = defaultdict(list)
    for node in nodes:
        by_label[node.label].append(node)
    for node_type in NODE_TYPES:
        index = next((i for i in FULL_TEXT_INDEXES if i.label == node_type.label), None)
        extra = (SEARCH_TEXT,) if index else ()
        query = (
            f"UNWIND $rows AS r CREATE (:{node_type.label} "
            f"{{{_assignments(node_type.properties, extra)}}})"
        )
        rows = []
        for node in by_label[node_type.label]:
            row = _row(node.id, node.episode_ids, node.properties)
            if index:
                row[SEARCH_TEXT] = _search_text(node.properties, index.fields)
            rows.append(row)
        _execute_in_chunks(conn, query, rows)


def _load_edges(conn: lb.Connection, edges: list[EdgeRecord], labels: Mapping[str, str]) -> None:
    groups: defaultdict[tuple[str, str, str], list[EdgeRecord]] = defaultdict(list)
    for edge in edges:
        groups[(edge.label, labels[edge.source], labels[edge.target])].append(edge)
    edge_types = {edge_type.label: edge_type for edge_type in EDGE_TYPES}
    for (label, source_label, target_label), group in groups.items():
        edge_type = edge_types[label]
        query = (
            f"UNWIND $rows AS r "
            f"MATCH (a:{source_label} {{id: r.source}}), (b:{target_label} {{id: r.target}}) "
            f"CREATE (a)-[:{label} {{{_assignments(edge_type.properties)}}}]->(b)"
        )
        rows = []
        for edge in group:
            row = _row(edge.id, edge.episode_ids, edge.properties)
            row["source"], row["target"] = edge.source, edge.target
            rows.append(row)
        _execute_in_chunks(conn, query, rows)


def _row(record_id: str, episode_ids: tuple[str, ...], properties: Mapping[str, Any]) -> dict:
    row: dict[str, Any] = {"id": record_id, "episode_ids": list(episode_ids)}
    for name, value in properties.items():
        row[name] = _engine_value(value)
    return row


def _engine_value(value: Any) -> Any:
    # LadybugDB stores an aware datetime's wall time and drops its offset, so store UTC.
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, tuple):
        return list(value)
    return value


def _search_text(properties: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    words: list[str] = []
    for name in fields:
        value = properties.get(name)
        if isinstance(value, str):
            words.append(value)
        elif isinstance(value, list | tuple):
            words.extend(str(item) for item in value)
    return " ".join(words)


def _execute_in_chunks(conn: lb.Connection, query: str, rows: list[dict]) -> None:
    for start in range(0, len(rows), _CHUNK):
        conn.execute(query, {"rows": rows[start : start + _CHUNK]})


def _check_counts(
    conn: lb.Connection, records: GraphRecords
) -> tuple[dict[str, int], dict[str, int]]:
    expected_nodes, expected_edges = records.node_counts(), records.edge_counts()
    nodes = {label: _count(conn, f"MATCH (n:{label}) RETURN count(n)") for label in expected_nodes}
    edges = {
        label: _count(conn, f"MATCH ()-[e:{label}]->() RETURN count(e)") for label in expected_edges
    }
    if nodes != expected_nodes or edges != expected_edges:
        raise BuildError(
            f"the build's counts don't match its records: nodes {nodes} (expected "
            f"{expected_nodes}), edges {edges} (expected {expected_edges})"
        )
    return nodes, edges


def _count(conn: lb.Connection, query: str) -> int:
    result = conn.execute(query)
    assert isinstance(result, lb.QueryResult)
    row = result.get_next()
    assert isinstance(row, list)
    return int(row[0])


def _seal(directory: Path) -> None:
    """Refuse a build holding anything but its database file, then make it read-only."""
    contents = sorted(path.name for path in directory.iterdir())
    if contents != [DATABASE_FILE]:
        raise BuildError(
            f"the build at {directory} holds {contents}, not just {DATABASE_FILE}; "
            "a leftover write-ahead log would be replayed by readers"
        )
    database = directory / DATABASE_FILE
    with database.open("rb") as handle:
        os.fsync(handle.fileno())
    database.chmod(0o444)
    directory.chmod(0o555)
    _fsync_dir(directory)


def _remove_build(directory: Path) -> None:
    if not directory.exists():
        return
    directory.chmod(stat.S_IRWXU)
    shutil.rmtree(directory)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")
