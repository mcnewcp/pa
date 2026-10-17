"""The current build's schema, read from the build itself, and the dialect notes `schema()`
gives the assistant for writing its own queries."""

from __future__ import annotations

import ladybug as lb

from pa_core.claims.queries import Connection, Index, Property, Table
from pa_home.graph.reader import CypherRunner, string_literal

LANGUAGE = f"Cypher (LadybugDB {lb.__version__})"

DIALECT_NOTES = (
    "One statement per call, with no `;` between statements.",
    "Find people with `CALL QUERY_FTS_INDEX('Person', 'person_fts', $text) RETURN node, score`"
    " and keep `score > 0`; a word found in everyone scores 0. Exact names, aliases, and "
    "addresses are better matched in Cypher first, as find_exact does.",
    "Bound variable-length paths: `-[*1..4]-`, or `-[* SHORTEST 1..8]-` and "
    "`-[* ALL SHORTEST 1..8]-` for connections. A bare `-[*]-` means up to 30 hops and will "
    "be stopped.",
    "Return a relationship with both its nodes, or return the whole path `p`.",
    "There are no list or pattern comprehensions: use `list_transform(l, x -> f(x))`, "
    "`list_filter(l, x -> cond)`, `EXISTS { MATCH … }`, and `COUNT { MATCH … }`.",
    "`WITH … ORDER BY` needs a `SKIP` or `LIMIT` after it (`SKIP 0` will do).",
    "`end` is reserved; name columns such as `starts_at` and `ends_at`.",
    "For an optional parameter, test `CAST($p AS STRING) IS NULL`; a bare `$p IS NULL` fails "
    "to bind when it is null.",
    "Timestamps are stored in UTC without an offset; compare them with "
    "`CAST($since AS TIMESTAMP)`.",
)


def live_tables(runner: CypherRunner) -> list[Table]:
    tables = []
    for row in runner.run("CALL show_tables() RETURN name, type ORDER BY type, name"):
        name, kind = row["name"], row["type"]
        quoted = string_literal(name)
        properties = [
            Property(name=column["name"], type=column["type"])
            for column in runner.run(f"CALL table_info('{quoted}') RETURN name, type")
        ]
        connections = []
        if kind == "REL":
            connections = [
                Connection(source=pair["source"], target=pair["target"])
                for pair in runner.run(
                    f"CALL show_connection('{quoted}') "
                    "RETURN `source table name` AS source, `destination table name` AS target"
                )
            ]
        tables.append(Table(name=name, kind=kind, properties=properties, connections=connections))
    return tables


def live_indexes(runner: CypherRunner) -> list[Index]:
    """The indexes a query can call; the primary-key hash indexes are left out."""
    return [
        Index(
            table=row["table_name"],
            name=row["index_name"],
            type=row["index_type"],
            properties=list(row["property_names"]),
        )
        for row in runner.run("CALL show_indexes() RETURN * ORDER BY table_name, index_name")
        if row["index_type"] != "HASH"
    ]
