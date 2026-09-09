"""Implements Sqlite store for code graph."""

import json
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cgis.core.freshness import Freshness, FreshnessState
from cgis.core.models import (
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.core.paths import is_test_path

RAW_CALL_PREFIX = "raw_call:"


@dataclass
class EdgeStats:
    """Aggregated edge statistics returned by get_edge_stats()."""

    total: int
    resolved: int  # edges whose target is an INTERNAL node
    stdlib: int  # edges whose target is a STDLIB virtual node
    external: int  # edges whose target is an EXTERNAL virtual node
    unresolved: (
        int  # edges whose target is UNKNOWN or raw_call: (post-resolver only UNKNOWN matters)
    )
    unresolved_ratio: float  # (unresolved + unknown) / total
    top_unresolved: list[tuple[str, int]] = field(default_factory=list)  # top UNKNOWN targets


class SQLiteStore:
    """
    Deterministic SQLite Graph Store.
    Manages persistence of Nodes and Edges with high performance indexing.
    """

    def __init__(self, db_path: str) -> None:
        """Initialise the store with a path to the SQLite database file."""
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._error_message = "Database not connected."

    def connect(self) -> None:
        """Establishes connection and initializes database schema."""
        if self._conn is not None:
            return
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._create_schema()

    def disconnect(self) -> None:
        """Close the SQLite connection if open."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SQLiteStore":
        """Connect and return self for use as a context manager."""
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Disconnect on context manager exit regardless of exception state."""
        self.disconnect()

    def _create_schema(self) -> None:
        """Create nodes, edges, and files_state tables if they do not yet exist."""
        schema = """
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            language TEXT NOT NULL,
            ontology_class TEXT,
            domains TEXT,
            confidence_score REAL NOT NULL,
            metadata TEXT,
            namespace TEXT NOT NULL DEFAULT 'INTERNAL',
            is_test INTEGER NOT NULL DEFAULT 0,
            is_generated INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL,
            confidence REAL NOT NULL,
            context TEXT,
            file_path TEXT,
            line_number INTEGER
        );

        CREATE TABLE IF NOT EXISTS files_state (
            file_path TEXT PRIMARY KEY,
            hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ingest_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_file_path ON edges(file_path);
        """
        if self._conn:
            self._conn.executescript(schema)
            self._conn.commit()
            self._migrate()

    def _migrate(self) -> None:
        """Apply incremental schema migrations for existing databases."""
        if not self._conn:
            return
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "namespace" not in cols:
            self._add_column_if_missing("namespace", "TEXT NOT NULL DEFAULT 'INTERNAL'")
            self._conn.commit()
        if "is_test" not in cols:
            self._add_column_if_missing("is_test", "INTEGER NOT NULL DEFAULT 0")
            self._backfill_is_test()
            self._conn.commit()
        if "is_generated" not in cols:
            # No backfill, unlike is_test: the marker is in the file header, which
            # this database does not keep, so it cannot be re-derived from stored
            # rows. An older graph reports zero generated nodes until re-ingest —
            # `OrphanReport.generated_excluded` is what makes that visible (#432).
            self._add_column_if_missing("is_generated", "INTEGER NOT NULL DEFAULT 0")
            # And the hashes are invalidated, or "re-ingest" is advice that cannot
            # be followed: `_process_file` skips any file whose content hash still
            # matches and reuses its stored nodes, so an incremental run over an
            # upgraded database would re-parse nothing and leave the column false
            # forever.
            #
            # Blanked, not deleted. `_persist_incremental` computes its stale set
            # as `get_all_tracked_files() - found_file_paths`, and that reads this
            # table — so dropping the rows would make a file deleted *before* the
            # upgrade unknowable, and its nodes would survive every later ingest.
            # An empty string never equals a hex digest, so every file re-parses
            # exactly as it would have, and stale detection keeps working.
            #
            # This fires on the first open of an old graph whatever opened it, so
            # a read-only `cgis orphans` invalidates them too. Deliberate: the
            # invalidation is required for correctness whenever it happens, first
            # open is the earliest moment it can happen, and the only cost is that
            # the next incremental ingest is a full one — which that graph needs
            # anyway. Deferring it to ingest would leave every query in between
            # reading a column that is silently false.
            self._conn.execute("UPDATE files_state SET hash = ''")
            self._conn.commit()

    def _add_column_if_missing(self, name: str, ddl: str) -> None:
        """Add a column to `nodes`, tolerating another process having just added it.

        `_migrate` reads `PRAGMA table_info` and then issues `ALTER TABLE`, and two
        cgis processes opening the same old graph — the MCP server and a CLI run,
        which is the ordinary workflow here — both see the column missing and both
        issue it. SQLite fails the loser with "duplicate column name", and
        `cli.orphans` does not wrap the store open, so that reached the user as a
        traceback rather than the ❌ every other failure gets. Only that one error
        is swallowed; anything else still raises (#441).
        """
        if not self._conn:
            return
        try:
            self._conn.execute(f"ALTER TABLE nodes ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def _backfill_is_test(self) -> None:
        """Populate the new column from the paths already stored (spec D5).

        `is_test_path` is pure and `file_path` is in every row, so a migrated
        graph does not have to be re-ingested to answer the orphan query
        correctly — without this it would report every test as production code
        and silently under-report. One pass, at migration time only.
        """
        if not self._conn:
            return
        rows = self._conn.execute("SELECT id, file_path FROM nodes").fetchall()
        test_ids = [(row["id"],) for row in rows if is_test_path(row["file_path"] or "")]
        if test_ids:
            self._conn.executemany("UPDATE nodes SET is_test = 1 WHERE id = ?", test_ids)

    _NODE_INSERT = """
        INSERT OR REPLACE INTO nodes (
            id, type, name, file_path, start_line, end_line, language,
            ontology_class, domains, confidence_score, metadata, namespace, is_test,
            is_generated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    _EDGE_INSERT = """
        INSERT OR REPLACE INTO edges (
            id, source, target, type, weight, confidence,
            context, file_path, line_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

    def _node_to_row(
        self, n: Node
    ) -> tuple[str, str, str, str, int, int, str, str | None, str, float, str, str, int, int]:
        """Serialise a Node into a tuple matching the nodes table column order."""
        return (
            n.id,
            n.type.value,
            n.name,
            n.file_path,
            n.start_line,
            n.end_line,
            n.language,
            n.ontology_class,
            json.dumps(n.domains),
            n.confidence_score,
            json.dumps(n.metadata),
            n.namespace.value,
            int(n.is_test),
            int(n.is_generated),
        )

    def _edge_to_row(
        self, e: Edge
    ) -> tuple[str, str, str, str, float, float, str | None, str | None, int | None]:
        """Serialise an Edge into a tuple matching the edges table column order."""
        return (
            e.id,
            e.source,
            e.target,
            e.type.value,
            e.weight,
            e.confidence,
            e.context,
            e.file_path,
            e.line_number,
        )

    def upsert_nodes(self, nodes: list[Node]) -> None:
        """Insert or replace nodes without deleting existing ones first."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            self._conn.executemany(self._NODE_INSERT, [self._node_to_row(n) for n in nodes])

    def upsert_edges(self, edges: list[Edge]) -> None:
        """Insert or replace edges without deleting existing ones first."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            self._conn.executemany(self._EDGE_INSERT, [self._edge_to_row(e) for e in edges])

    def delete_semantic_uplift(self) -> None:
        """Remove all DOMAIN_CONCEPT nodes and DOMAIN_DEPENDS_ON edges."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            self._conn.execute("DELETE FROM nodes WHERE type = ?", (NodeType.DOMAIN_CONCEPT.value,))
            self._conn.execute(
                "DELETE FROM edges WHERE type = ?", (EdgeType.DOMAIN_DEPENDS_ON.value,)
            )

    def get_all_nodes(self) -> list[Node]:
        """Return every node currently in the store."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM nodes")
        return [self._row_to_node(row) for row in cursor.fetchall()]

    def get_referenced_targets(
        self, edge_types: Iterable[EdgeType], *, from_test_sources: bool = True
    ) -> set[str]:
        """Distinct targets of edges of these types, optionally from production sources only.

        Answers "what does anything point at" without materialising the edges.
        The orphan query needs a set of ids out of 75 000 edges on a mid-sized
        backend; going through `get_all_edges` builds 75 000 Pydantic models to
        throw all but the target away, which is where its 168 MB went — and 1.8 GB
        on a 1M-edge graph. This does it in SQL, which is what the store is for.
        """
        types = sorted({t.value for t in edge_types})
        if not types:
            return set()
        placeholders = ",".join("?" * len(types))
        source_filter = "" if from_test_sources else " AND n.is_test = 0"
        # A join rather than a subquery: an edge whose source is not a node at
        # all (a virtual boundary target has no row until the resolver mints
        # one) must not count as a production reference.
        sql = (
            "SELECT DISTINCT e.target FROM edges e "
            "JOIN nodes n ON e.source = n.id "
            f"WHERE e.type IN ({placeholders}){source_filter}"
        )
        rows = self._conn.execute(sql, types).fetchall() if self._conn else []
        return {row[0] for row in rows}

    def get_all_edges(self) -> list[Edge]:
        """Return every edge currently in the store."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM edges")
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def save_graph(self, nodes: list[Node], edges: list[Edge], overwrite: bool = False) -> None:
        """Persists all nodes and edges inside a single transaction."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            if overwrite:
                self._conn.execute("DELETE FROM nodes")
                self._conn.execute("DELETE FROM edges")
            self._conn.executemany(self._EDGE_INSERT, [self._edge_to_row(e) for e in edges])
            self._conn.executemany(self._NODE_INSERT, [self._node_to_row(n) for n in nodes])

    def clear(self) -> None:
        """Wipe the whole graph — nodes, edges, and the incremental files_state cache.

        Used for a full rebuild: re-scanning from an empty database drops nodes
        for deleted/renamed files, and clearing ``files_state`` keeps the
        incremental cache consistent with what the re-scan repopulates (so the
        next incremental run isn't forced to re-parse everything).
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM files_state")

    def get_node_count(self) -> int:
        """Return the total node count via a cheap COUNT(*) (no deserialization)."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        row = self._conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
        return int(row["n"]) if row else 0

    def get_edge_count(self) -> int:
        """Return the total edge count via a cheap COUNT(*) (no join/aggregation)."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        row = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
        return int(row["n"]) if row else 0

    def get_node(self, node_id: str) -> Node | None:
        """Return a single node by FQN, or None if not found."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_node(row)

    def find_nodes_by_suffix(self, name: str, limit: int = 10) -> list[Node]:
        """Find nodes whose FQN ends with ``.name`` at a dot boundary.

        Returns only dot-boundary suffix matches ordered by id, capped at
        ``limit``. The id itself is NOT its own dot-boundary suffix — exact
        match policy (``get_node`` short-circuit) lives in the caller
        (``resolve_fqn``). LIKE wildcards in ``name`` (``%``, ``_``) are
        escaped — they are literal characters in FQNs.

        This method was intentionally kept pure (no intra-storage ``get_node``
        call) to avoid a CALLS chain inside the ``cgis.storage`` domain that
        would violate the pure_utility pattern's 021D triad constraint — the
        self-drift guardrail caught this coupling smell during development.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = self._conn.execute(
            "SELECT * FROM nodes WHERE id LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?",
            (f"%.{escaped}", limit),
        )
        return [self._row_to_node(row) for row in cursor.fetchall()]

    def search_nodes(
        self,
        query: str,
        kinds: tuple[str, ...] = (),
        fqn_prefix: str | None = None,
        limit: int = 20,
    ) -> list[Node]:
        """Find nodes whose leaf ``name`` contains ``query`` (substring), ranked.

        Ranking: exact name match > name-prefix match > substring; ties broken by
        shorter FQN then id (deterministic). ``kinds`` filters by NodeType value
        (e.g. ``("FUNCTION", "METHOD")``); ``fqn_prefix`` scopes to ids under that
        prefix. LIKE wildcards (``%``, ``_``) in inputs are escaped — they are
        literal characters in names/FQNs. Powers ``cgis_find_symbol`` (#173).
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        if not query.strip():
            return []
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = "SELECT * FROM nodes WHERE name LIKE ? ESCAPE '\\'"
        params: list[str | int] = [f"%{esc}%"]
        if kinds:
            placeholders = ", ".join(["?"] * len(kinds))
            sql += f" AND type IN ({placeholders})"
            params.extend(kinds)
        if fqn_prefix:
            pfx = fqn_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            # Segment-boundary match: the prefix itself or a child under it — never
            # `app.svc` accidentally matching `app.svc_alternative` (mirrors _in_domain).
            sql += " AND (id = ? OR id LIKE ? ESCAPE '\\')"
            params.extend([fqn_prefix, f"{pfx}.%"])
        sql += (
            " ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,"
            " LENGTH(id), id LIMIT ?"
        )
        params.extend([query, f"{esc}%", limit])
        cursor = self._conn.execute(sql, params)
        return [self._row_to_node(row) for row in cursor.fetchall()]

    def get_nodes(self, node_ids: list[str]) -> list[Node]:
        """Return nodes matching the given FQN list, fetched in 999-item chunks."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        if not node_ids:
            return []
        unique_ids = list(set(node_ids))
        # SQLite has a limit on the number of host parameters (usually 999)
        chunk_size = 999
        nodes = []
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            placeholders = ", ".join(["?"] * len(chunk))
            query = f"SELECT * FROM nodes WHERE id IN ({placeholders})"
            cursor = self._conn.execute(query, chunk)
            nodes.extend([self._row_to_node(row) for row in cursor.fetchall()])
        return nodes

    def get_outgoing_edges(self, node_id: str) -> list[Edge]:
        """Return all edges where the given node is the source."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM edges WHERE source = ?", (node_id,))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_incoming_edges(self, node_id: str) -> list[Edge]:
        """Return all edges where the given node is the target."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM edges WHERE target = ?", (node_id,))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_outgoing_edges_batch(self, node_ids: list[str]) -> list[Edge]:
        """Fetch all outgoing edges for a set of nodes in one query per chunk."""
        return self._get_edges_batch(node_ids, column="source")

    def get_incoming_edges_batch(self, node_ids: list[str]) -> list[Edge]:
        """Fetch all incoming edges for a set of nodes in one query per chunk."""
        return self._get_edges_batch(node_ids, column="target")

    def _get_edges_batch(self, node_ids: list[str], column: str) -> list[Edge]:
        """Fetch edges for many nodes at once, chunked to respect SQLite's 999-param limit."""
        if column not in ("source", "target"):
            msg = f"Invalid column: {column!r}. Must be 'source' or 'target'."
            raise ValueError(msg)
        if not self._conn:
            raise RuntimeError(self._error_message)
        if not node_ids:
            return []
        unique_ids = list(set(node_ids))
        chunk_size = 999
        edges: list[Edge] = []
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i : i + chunk_size]
            placeholders = ", ".join(["?"] * len(chunk))
            query = f"SELECT * FROM edges WHERE {column} IN ({placeholders})"
            cursor = self._conn.execute(query, chunk)
            edges.extend(self._row_to_edge(row) for row in cursor.fetchall())
        return edges

    def get_file_hash(self, file_path: str) -> str | None:
        """Return the stored hash for a file, or None if not yet tracked."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute(
            "SELECT hash FROM files_state WHERE file_path = ?", (file_path,)
        )
        row = cursor.fetchone()
        return str(row["hash"]) if row else None

    def upsert_file_hash(self, file_path: str, file_hash: str) -> None:
        """Insert or update the hash for a file."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO files_state (file_path, hash) VALUES (?, ?)",
                (file_path, file_hash),
            )

    def delete_file_data(self, file_path: str) -> None:
        """Delete all nodes and edges associated with a file."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        with self._conn:
            # By source (handles structural edges with file_path=None) and by file_path
            self._conn.execute(
                "DELETE FROM edges WHERE source IN (SELECT id FROM nodes WHERE file_path = ?)",
                (file_path,),
            )
            self._conn.execute("DELETE FROM edges WHERE file_path = ?", (file_path,))
            self._conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
            self._conn.execute("DELETE FROM files_state WHERE file_path = ?", (file_path,))

    def save_incremental_batch(
        self,
        nodes_by_file: dict[str, list[Node]],
        edges_by_file: dict[str, list[Edge]],
        file_hashes: dict[str, str],
        stale_files: set[str],
    ) -> None:
        """Atomically delete changed/stale files and insert new data in one transaction."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        all_changed = set(file_hashes) | stale_files
        with self._conn:
            for file_path in all_changed:
                self._conn.execute(
                    "DELETE FROM edges WHERE source IN (SELECT id FROM nodes WHERE file_path = ?)",
                    (file_path,),
                )
                self._conn.execute("DELETE FROM edges WHERE file_path = ?", (file_path,))
                self._conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
                self._conn.execute("DELETE FROM files_state WHERE file_path = ?", (file_path,))
            for nodes in nodes_by_file.values():
                self._conn.executemany(self._NODE_INSERT, [self._node_to_row(n) for n in nodes])
            for edges in edges_by_file.values():
                self._conn.executemany(self._EDGE_INSERT, [self._edge_to_row(e) for e in edges])
            for file_path, hash_val in file_hashes.items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO files_state (file_path, hash) VALUES (?, ?)",
                    (file_path, hash_val),
                )

    def get_nodes_by_file(self, file_path: str) -> list[Node]:
        """Return all nodes belonging to a specific file."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM nodes WHERE file_path = ?", (file_path,))
        return [self._row_to_node(row) for row in cursor.fetchall()]

    def get_structural_subgraph(
        self, target_id: str, max_depth: int = 5
    ) -> tuple[list[Node], list[Edge]]:
        """Return the structural hierarchy rooted at target_id via a single recursive CTE.

        Traverses only CONTAINS and DECLARES edges. One DB round-trip regardless of depth.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        self._conn.execute("PRAGMA recursive_triggers = ON")
        nodes_rows = self._conn.execute(
            """
            WITH RECURSIVE tree(id, depth) AS (
                SELECT id, 0 FROM nodes WHERE id = ?
                UNION ALL
                SELECT e.target, t.depth + 1
                FROM edges e
                JOIN tree t ON e.source = t.id
                WHERE e.type IN ('CONTAINS', 'DECLARES') AND t.depth < ?
            )
            SELECT DISTINCT n.*
            FROM nodes n
            JOIN tree t ON n.id = t.id
            """,
            (target_id, max_depth),
        ).fetchall()
        if not nodes_rows:
            return [], []
        nodes = [self._row_to_node(row) for row in nodes_rows]
        edges_rows = self._conn.execute(
            """
            WITH RECURSIVE tree(id, depth) AS (
                SELECT id, 0 FROM nodes WHERE id = ?
                UNION ALL
                SELECT e.target, t.depth + 1
                FROM edges e
                JOIN tree t ON e.source = t.id
                WHERE e.type IN ('CONTAINS', 'DECLARES') AND t.depth < ?
            )
            SELECT DISTINCT e.*
            FROM edges e
            JOIN tree t ON e.source = t.id
            WHERE e.type IN ('CONTAINS', 'DECLARES') AND t.depth < ?
            """,
            (target_id, max_depth, max_depth),
        ).fetchall()
        edges = [self._row_to_edge(row) for row in edges_rows]
        return nodes, edges

    def get_edge_stats(self) -> EdgeStats:
        """Return resolution statistics for all edges in the graph."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN n.namespace = 'INTERNAL' THEN 1 END) AS resolved,
                COUNT(CASE WHEN n.namespace = 'STDLIB'   THEN 1 END) AS stdlib,
                COUNT(CASE WHEN n.namespace = 'EXTERNAL' THEN 1 END) AS external,
                COUNT(CASE WHEN n.namespace IS NULL OR n.namespace = 'UNKNOWN'
                           OR e.target GLOB ? THEN 1 END) AS unresolved
            FROM edges e
            LEFT JOIN nodes n ON e.target = n.id
            """,
            (f"{RAW_CALL_PREFIX}*",),
        ).fetchone()
        if row is None:
            return EdgeStats(0, 0, 0, 0, 0, 0.0)
        total: int = row["total"]
        resolved: int = row["resolved"]
        stdlib: int = row["stdlib"]
        external: int = row["external"]
        unresolved: int = row["unresolved"]
        ratio = unresolved / total if total else 0.0
        rows = self._conn.execute(
            """
            SELECT e.target, COUNT(*) AS cnt
            FROM edges e
            LEFT JOIN nodes n ON e.target = n.id
            WHERE n.namespace = 'UNKNOWN' OR n.id IS NULL
            GROUP BY e.target
            ORDER BY cnt DESC
            LIMIT 10
            """,
        ).fetchall()
        top = [(r["target"], int(r["cnt"])) for r in rows]
        return EdgeStats(
            total=total,
            resolved=resolved,
            stdlib=stdlib,
            external=external,
            unresolved=unresolved,
            unresolved_ratio=ratio,
            top_unresolved=top,
        )

    def freshness(self, root: str | None = None) -> Freshness:
        """Does this graph still match the tree it was built from? (#175)

        Decided by `st_mtime` against the recorded ingest time, never by
        re-hashing: statting a known list is ~4 ms on an 800-file repository,
        which is noise next to a query, while hashing is seconds. `touch` on an
        unmodified file therefore reports `STALE` when nothing changed. That is
        the chosen direction — over-reporting costs a re-ingest, under-reporting
        returns a confident wrong answer, which is the failure this exists to
        remove.

        Statting a known list rather than walking the tree, because a walk would
        have to reproduce `IngestionPipeline`'s directory exclusions or count
        every file in a `.venv` as new. The two halves are complementary and
        measured: a file's own mtime catches an edit, and its *directory's* mtime
        catches an addition, a deletion and a new subdirectory — a directory's
        mtime does not move when a file inside it is edited.

        Tracked files come from `nodes.file_path`, not `files_state`: a
        non-incremental `cgis ingest` leaves that table empty, so a probe built
        on it would report "nothing missing" for every ordinary graph.
        """
        recorded = self.get_ingest_state()
        if recorded is None:
            return Freshness(
                state=FreshnessState.UNKNOWN,
                reason="graph predates the ingest_state table — re-ingest to enable the check",
            )
        recorded_root, ingested_at = recorded
        base = root or recorded_root
        if not os.path.isdir(base):  # noqa: PTH112 - see the note on pathlib cost below
            return Freshness(
                state=FreshnessState.UNKNOWN,
                reason=f"ingest root {recorded_root} no longer exists — pass an explicit root",
            )

        tracked = self.get_tracked_source_files()
        changed = 0
        missing = 0
        # `os.stat` on joined strings, not pathlib — the PTH rules are suppressed
        # deliberately here. Measured on this repository's own graph: the same 811
        # files cost 3.7 ms this way and 18.8 ms through `Path.stat()`, which is
        # slower than the tree walk this design exists to avoid. Tidying these to
        # `Path` would triple a cost paid on every query.
        for rel in tracked:
            try:
                if os.stat(os.path.join(base, rel)).st_mtime > ingested_at:  # noqa: PTH116,PTH118
                    changed += 1
            except OSError:
                missing += 1

        for rel_dir in {os.path.dirname(rel) for rel in tracked}:  # noqa: PTH120
            try:
                path = os.path.join(base, rel_dir)  # noqa: PTH118
                if os.stat(path).st_mtime > ingested_at:  # noqa: PTH116
                    changed += self._directory_gained_a_source(path, tracked, base, ingested_at)
            except OSError:
                missing += 1

        if changed or missing:
            return Freshness(state=FreshnessState.STALE, changed=changed, missing=missing)
        return Freshness(state=FreshnessState.FRESH)

    def _directory_gained_a_source(
        self, path: str, tracked: set[str], base: str, ingested_at: float
    ) -> int:
        """1 when a newer directory holds something the graph should have seen.

        A directory's mtime moves for any write into it, not only for a new source
        file — this database itself is the common case, since `cgis ingest . -o
        graph.db` puts it in the tree it describes and finishes writing *after*
        the ingest is recorded. Reported as stale, that made the default usage
        permanently stale. Editor swap files and a freshly created `__pycache__`
        are the same shape.

        So a suspicious directory is opened once and asked the sharper question:
        does it hold an entry that is newer than the ingest, is not already in the
        graph, and is not this database? Only then did it really gain something.
        Scanning happens solely for directories that already look changed, so the
        ceiling is one pass over the tree — the cost this design avoids paying on
        every query.
        """
        # Absolute on both sides: `-o graph.db` stores a relative path while
        # scandir yields absolute ones, and a mismatch would let the database
        # count as a new source file — the very case this exists to exclude.
        db_abs = os.path.abspath(self.db_path)  # noqa: PTH100
        db_family = {db_abs, f"{db_abs}-wal", f"{db_abs}-shm"}
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if not entry.is_file() or os.path.abspath(entry.path) in db_family:  # noqa: PTH100
                        continue
                    rel = os.path.relpath(entry.path, base)
                    if rel not in tracked and entry.stat().st_mtime > ingested_at:
                        return 1
        except OSError:
            # Unreadable now but statted a moment ago: report it rather than
            # silently treating the directory as unchanged.
            return 1
        return 0

    def get_tracked_source_files(self) -> set[str]:
        """The real source files this graph was built from, as stored paths.

        `VIRTUAL_FILE_PATH` is excluded: resolver-minted boundary nodes have no
        file behind them and would read as permanently missing.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute(
            "SELECT DISTINCT file_path FROM nodes WHERE file_path != ?", (VIRTUAL_FILE_PATH,)
        )
        return {row["file_path"] for row in cursor.fetchall()}

    def record_ingest(self, root: str) -> None:
        """Record what this graph was built from, for the freshness probe (#175).

        `root` is stored absolute: a relative path is meaningless to a later
        process with a different working directory.

        `ingested_at` is the **largest mtime among the ingested files**, not the
        wall clock. Measured on this filesystem, every one of 200 writes received
        an mtime 2.5-6.4 ms *earlier* than a `time.time()` reading taken before
        the write, because mtimes are quantised and rounded down. A wall-clock
        mark therefore misses any edit made near the ingest — under-reporting,
        the one direction this signal must not fail in. Subtracting a fixed
        margin would instead leave a permanent false `STALE`. Taking the maximum
        puts both sides of the later comparison on the same clock, from the same
        source, so the skew cancels.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        self._conn.executemany(
            "INSERT OR REPLACE INTO ingest_state (key, value) VALUES (?, ?)",
            [
                ("root", str(Path(root).resolve())),
                ("ingested_at", str(self._max_source_mtime(root))),
            ],
        )
        self._conn.commit()

    def _max_source_mtime(self, root: str) -> float:
        """The newest mtime among the files this graph was built from, and their dirs.

        Falls back to the wall clock for a graph with no source files, where
        there is nothing to take a maximum over.
        """
        newest = 0.0
        for rel in self.get_tracked_source_files():
            joined = os.path.join(root, rel)  # noqa: PTH118 - see freshness() on pathlib cost
            for candidate in (joined, os.path.dirname(joined)):  # noqa: PTH120
                try:
                    newest = max(newest, os.stat(candidate).st_mtime)  # noqa: PTH116
                except OSError:
                    continue
        return newest or time.time()

    def get_ingest_state(self) -> tuple[str, float] | None:
        """The recorded (root, ingested_at), or None on a graph that predates it.

        `None` rather than a default: a zero timestamp would make every older
        graph look freshly ingested in 1970, which is a `FRESH`-shaped answer to
        a question that cannot be answered.
        """
        if not self._conn:
            raise RuntimeError(self._error_message)
        rows = {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM ingest_state")
        }
        if not {"root", "ingested_at"} <= rows.keys():
            return None
        return rows["root"], float(rows["ingested_at"])

    def get_all_tracked_files(self) -> set[str]:
        """Return the set of all file paths currently tracked in files_state."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT file_path FROM files_state")
        return {row["file_path"] for row in cursor.fetchall()}

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        """Deserialise a SQLite row into a Node model."""
        return Node(
            id=row["id"],
            type=NodeType(row["type"]),
            name=row["name"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            language=row["language"],
            ontology_class=row["ontology_class"],
            domains=json.loads(row["domains"]) or [] if row["domains"] else [],
            confidence_score=row["confidence_score"],
            metadata=json.loads(row["metadata"]) or {} if row["metadata"] else {},
            # `in row.keys()`, not `in row`: sqlite3.Row iterates its *values*,
            # so the shorter form ruff suggests (SIM118) is always False here
            # and would silently mark every node as production code.
            is_test=bool(row["is_test"]) if "is_test" in row.keys() else False,  # noqa: SIM118
            is_generated=bool(row["is_generated"])
            if "is_generated" in row.keys()  # noqa: SIM118
            else False,
            namespace=NodeNamespace(row["namespace"])
            if row["namespace"]
            else NodeNamespace.INTERNAL,
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        """Deserialise a SQLite row into an Edge model."""
        return Edge(
            id=row["id"],
            source=row["source"],
            target=row["target"],
            type=EdgeType(row["type"]),
            weight=row["weight"],
            confidence=row["confidence"],
            context=row["context"],
            file_path=row["file_path"],
            line_number=row["line_number"],
        )
