"""Canonical dump and diff of a stored cgis graph — the language-packs identity net.

Steps 1-5 of docs/specs/2026-10-14-language-packs-design.md move code without
changing behaviour. This is how that is checked: the same input ingested by two
cgis versions must dump identically. Absolute paths become `<ROOT>` and the
mtime-derived `ingested_at` is dropped, so a dump compares across machines and
temp directories.
"""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import cast

import cgis
from cgis.languages import extractors
from cgis.pipeline import IngestionPipeline
from cgis.storage.sqlite_store import SQLiteStore

_NODE_COLUMNS = (
    "id", "type", "name", "file_path", "start_line", "end_line", "language",
    "namespace", "is_test", "is_generated", "metadata", "ontology_class",
    "domains", "confidence_score",
)  # fmt: skip
_EDGE_COLUMNS = (
    "id", "source", "target", "type", "weight", "confidence", "context",
    "file_path", "line_number", "type_only",
)  # fmt: skip
_VOLATILE_STATE = frozenset({"ingested_at", "extraction_fingerprint"})
_DB_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})

#: The corpus roots a snapshot ingests, by name. `sub` reads configs above its
#: root (#555); `py_src` is the src layout, where the local `alembic` package
#: meets the declared `alembic` dependency.
SNAPSHOT_ROOTS: dict[str, str] = {"whole": ".", "sub": "ts/apps/web/src", "py_src": "py/src"}


def _normalise(value: object, root: str) -> object:
    """`value` with the corpus root replaced by `<ROOT>`, JSON text parsed to data."""
    if isinstance(value, str):
        text = value.replace(root, "<ROOT>")
        if text[:1] in "[{":
            try:
                return _normalise(json.loads(text), root)
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
    """The canonical content of one stored graph, with `root` normalised away.

    `db_path` must be an existing database file: `sqlite3.connect` would create
    a missing one, and its URI-free path keeps the argument a plain file name.
    """
    if db_path.suffix not in _DB_SUFFIXES:
        msg = f"not a graph database (expected {sorted(_DB_SUFFIXES)}): {db_path}"
        raise ValueError(msg)
    if not db_path.is_file():
        msg = f"no graph database at {db_path}"
        raise FileNotFoundError(msg)
    prefix = str(root.resolve())
    with closing(sqlite3.connect(db_path)) as conn:  # `with conn` alone never closes
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


def _ingest(root: Path, db: Path) -> None:
    """One full ingest of `root` into `db`, as `cgis ingest` does it, ingest state included."""
    with SQLiteStore(str(db)) as store:
        pipeline = IngestionPipeline(extractors([]))
        pipeline.run(str(root), store=store)
        store.record_ingest(str(root), pipeline.observed_mtimes)
        store.record_ingest_options([], None)


def snapshot(corpus: Path, roots: Mapping[str, str] = SNAPSHOT_ROOTS) -> dict[str, object]:
    """Every snapshot root, ingested from a temp copy of `corpus` with a `.git` marker.

    `roots` names each ingest root, relative to the corpus: the Python/TS corpus's
    by default, `{"go": "."}` for the Go one (step 7b).

    The copy keeps the checked-in corpus free of `.git` (which git cannot hold)
    and makes the repository boundary of the upward config search (#555) the
    corpus itself, wherever the tests run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "corpus"
        shutil.copytree(corpus, copy, ignore=shutil.ignore_patterns("__pycache__"))
        (copy / ".git").mkdir()
        dumps: dict[str, object] = {}
        for name, relative in roots.items():
            db = Path(tmp) / f"{name}.db"
            _ingest(copy / relative, db)
            dumps[name] = dump(db, copy)
        return dumps


def main(argv: list[str] | None = None) -> int:
    """`diff` two stored graphs (exit 1 when they differ), or `snapshot` the corpus."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diff")
    for name in ("db_a", "root_a", "db_b", "root_b"):
        d.add_argument(name, type=Path)
    s = sub.add_parser("snapshot")
    s.add_argument("corpus", type=Path)
    s.add_argument("--out", type=Path)
    s.add_argument(
        "--root",
        action="append",
        metavar="NAME=PATH",
        help="an ingest root relative to the corpus; repeatable (default: the Python/TS roots)",
    )
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        pairs = [r.partition("=") for r in args.root or []]
        if any(not name or not sep for name, sep, _ in pairs):
            parser.error("--root wants NAME=PATH")
        roots = {name: path for name, _, path in pairs} if pairs else SNAPSHOT_ROOTS
        text = json.dumps(
            snapshot(args.corpus, roots), indent=1, sort_keys=True, ensure_ascii=False
        )
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    lines = diff(dump(args.db_a, args.root_a), dump(args.db_b, args.root_b))
    print("\n".join(lines) if lines else "identical")
    return 1 if lines else 0


if __name__ == "__main__":
    cgis.log_to_stderr()  # stdout carries the snapshot JSON (#619)
    sys.exit(main())
