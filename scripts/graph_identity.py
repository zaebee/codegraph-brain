"""Canonical dump and diff of a stored cgis graph — the language-packs identity net.

Steps 1-5 of docs/specs/2026-10-14-language-packs-design.md move code without
changing behaviour. This is how that is checked: the same input ingested by two
cgis versions must dump identically. Absolute paths become `<ROOT>` and the
mtime-derived `ingested_at` is dropped, so a dump compares across machines and
temp directories.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

_NODE_COLUMNS = (
    "id", "type", "name", "file_path", "start_line", "end_line", "language",
    "namespace", "is_test", "is_generated", "metadata",
)  # fmt: skip
_EDGE_COLUMNS = (
    "id", "source", "target", "type", "weight", "confidence", "context",
    "file_path", "line_number", "type_only",
)  # fmt: skip
_VOLATILE_STATE = frozenset({"ingested_at"})


def _normalise(value: object, root: str) -> object:
    """`value` with the corpus root replaced by `<ROOT>`, JSON text parsed to data."""
    if isinstance(value, str):
        text = value.replace(root, "<ROOT>")
        if text[:1] in "[{":
            try:
                return _normalise(json.loads(text), "\0")
            except ValueError:
                return text
        return text
    if isinstance(value, list):
        return [_normalise(v, root) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v, root) for k, v in sorted(value.items())}
    return value


def _rows(
    conn: sqlite3.Connection, table: str, columns: tuple[str, ...], root: str
) -> list[dict[str, object]]:
    """Every row of `table` as a normalised dict, sorted by id."""
    query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"  # table/columns are constants
    return [
        {col: _normalise(val, root) for col, val in zip(columns, row, strict=True)}
        for row in conn.execute(query)
    ]


def dump(db_path: Path, root: Path) -> dict[str, object]:
    """The canonical content of one stored graph, with `root` normalised away."""
    prefix = str(root.resolve())
    with sqlite3.connect(db_path) as conn:
        state = {
            key: _normalise(value, prefix)
            for key, value in conn.execute("SELECT key, value FROM ingest_state ORDER BY key")
            if key not in _VOLATILE_STATE
        }
        return {
            "nodes": _rows(conn, "nodes", _NODE_COLUMNS, prefix),
            "edges": _rows(conn, "edges", _EDGE_COLUMNS, prefix),
            "ingest_state": state,
        }


Row = dict[str, object]


def _by_id(dumped: Mapping[str, object], table: str) -> dict[object, Row]:
    """One table of a dump, keyed by row id."""
    return {row["id"]: row for row in cast("list[Row]", dumped[table])}


def diff(before: Mapping[str, object], after: Mapping[str, object]) -> list[str]:
    """One line per row that differs between two dumps, keyed by table and id."""
    lines: list[str] = []
    for table in ("nodes", "edges"):
        old, new = _by_id(before, table), _by_id(after, table)
        lines.extend(
            f"{table} {key}: {json.dumps(old.get(key))} → {json.dumps(new.get(key))}"
            for key in sorted(old.keys() | new.keys(), key=str)
            if old.get(key) != new.get(key)
        )
    if before["ingest_state"] != after["ingest_state"]:
        lines.append(f"ingest_state: {before['ingest_state']} → {after['ingest_state']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """`diff <db-a> <root-a> <db-b> <root-b>`: exit 1 and print when the graphs differ."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diff")
    for name in ("db_a", "root_a", "db_b", "root_b"):
        d.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    lines = diff(dump(args.db_a, args.root_a), dump(args.db_b, args.root_b))
    print("\n".join(lines) if lines else "identical")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
