"""Implements Sqlite store for code graph."""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType

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
            is_test INTEGER NOT NULL DEFAULT 0
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
            self._conn.execute(
                "ALTER TABLE nodes ADD COLUMN namespace TEXT NOT NULL DEFAULT 'INTERNAL'"
            )
            self._conn.commit()
        if "is_test" not in cols:
            # An existing graph gets 0 for every row, which reads as "all
            # production" — the behaviour before this column existed. Re-ingest
            # to populate it; the orphan query says so when it finds none.
            self._conn.execute("ALTER TABLE nodes ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()

    _NODE_INSERT = """
        INSERT OR REPLACE INTO nodes (
            id, type, name, file_path, start_line, end_line, language,
            ontology_class, domains, confidence_score, metadata, namespace, is_test
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    _EDGE_INSERT = """
        INSERT OR REPLACE INTO edges (
            id, source, target, type, weight, confidence,
            context, file_path, line_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

    def _node_to_row(
        self, n: Node
    ) -> tuple[str, str, str, str, int, int, str, str | None, str, float, str, str, int]:
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
