"""The node and edge records the librarian produces, for a storage adapter to load."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pa_core.claims.schema import EDGE_TYPES, NODE_TYPES, EdgeType, NodeType
from pa_core.errors import PaError


class InvalidRecordsError(PaError):
    """Records that don't fit the claims graph's schema."""


def stable_id(prefix: str, *parts: str) -> str:
    """`<prefix>_` plus the first 16 hex of SHA-256 over `parts`: the same inputs, the same id."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class NodeRecord:
    label: str
    id: str
    properties: Mapping[str, Any]
    episode_ids: tuple[str, ...]


@dataclass(frozen=True)
class EdgeRecord:
    label: str
    id: str
    source: str
    """The id of the node the edge comes from."""
    target: str
    properties: Mapping[str, Any]
    episode_ids: tuple[str, ...]


@dataclass
class GraphRecords:
    nodes: list[NodeRecord] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)

    def node_counts(self) -> dict[str, int]:
        """Nodes by label, every node type included."""
        counts = Counter(node.label for node in self.nodes)
        return {node_type.label: counts[node_type.label] for node_type in NODE_TYPES}

    def edge_counts(self) -> dict[str, int]:
        """Edges by label, every edge type included."""
        counts = Counter(edge.label for edge in self.edges)
        return {edge_type.label: counts[edge_type.label] for edge_type in EDGE_TYPES}

    def labels_by_id(self) -> dict[str, str]:
        return {node.id: node.label for node in self.nodes}

    def validate(self) -> None:
        """Raise InvalidRecordsError unless every record fits the schema.

        Each label is a declared type, each property is declared for it (all of them present),
        ids are unique, and every edge joins existing nodes of labels its type allows.
        """
        node_types = {t.label: t for t in NODE_TYPES}
        edge_types = {t.label: t for t in EDGE_TYPES}
        labels: dict[str, str] = {}
        for node in self.nodes:
            node_type = _declared(node_types, node.label, node.id)
            _check_properties(node_type, node.properties, node.id)
            if node.id in labels:
                raise InvalidRecordsError(f"two nodes have the id {node.id}")
            labels[node.id] = node.label
        edge_ids: set[str] = set()
        for edge in self.edges:
            edge_type = _declared(edge_types, edge.label, edge.id)
            _check_properties(edge_type, edge.properties, edge.id)
            if edge.id in edge_ids:
                raise InvalidRecordsError(f"two edges have the id {edge.id}")
            edge_ids.add(edge.id)
            pair = (labels.get(edge.source), labels.get(edge.target))
            if pair not in edge_type.endpoints:
                raise InvalidRecordsError(
                    f"edge {edge.id} ({edge.label}) joins {pair}, not one of {edge_type.endpoints}"
                )


def _declared[T](types: Mapping[str, T], label: str, record_id: str) -> T:
    if label not in types:
        raise InvalidRecordsError(f"{record_id} has the undeclared label {label}")
    return types[label]


def _check_properties(
    declared: NodeType | EdgeType, properties: Mapping[str, Any], rid: str
) -> None:
    if set(properties) != set(declared.properties):
        raise InvalidRecordsError(
            f"{rid} ({declared.label}) has properties {sorted(properties)}, "
            f"expected {sorted(declared.properties)}"
        )
