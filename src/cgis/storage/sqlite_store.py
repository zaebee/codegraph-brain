"""Implements Sqlite store for code graph."""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from cgis.core.models import Edge, EdgeType, Node, NodeType

RAW_CALL_PREFIX = "raw_call:"


@dataclass
class EdgeStats:
    total: int
    resolved: int
    unresolved: int
    unresolved_ratio: float
    top_unresolved: list[tuple[str, int]] = field(default_factory=list)


class SQLiteStore:
    """
    Deterministic SQLite Graph Store.
    Manages persistence of Nodes and Edges with high performance indexing.
    """

    def __init__(self, db_path: str) -> None:
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
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SQLiteStore":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.disconnect()

    def _create_schema(self) -> None:
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
            metadata TEXT
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

    _NODE_INSERT = """
        INSERT OR REPLACE INTO nodes (
            id, type, name, file_path, start_line, end_line, language,
            ontology_class, domains, confidence_score, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    _EDGE_INSERT = """
        INSERT OR REPLACE INTO edges (
            id, source, target, type, weight, confidence,
            context, file_path, line_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

    def _node_to_row(
        self, n: Node
    ) -> tuple[str, str, str, str, int, int, str, str | None, str, float, str]:
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
        )

    def _edge_to_row(
        self, e: Edge
    ) -> tuple[str, str, str, str, float, float, str | None, str | None, int | None]:
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

    def get_node(self, node_id: str) -> Node | None:
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_node(row)

    def get_nodes(self, node_ids: list[str]) -> list[Node]:
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
        if not self._conn:
            raise RuntimeError(self._error_message)
        cursor = self._conn.execute("SELECT * FROM edges WHERE source = ?", (node_id,))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_incoming_edges(self, node_id: str) -> list[Edge]:
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

    def get_edge_stats(self) -> EdgeStats:
        """Return resolution statistics for all edges in the graph."""
        if not self._conn:
            raise RuntimeError(self._error_message)
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(CASE WHEN target LIKE ? THEN 1 END) FROM edges",
            (f"{RAW_CALL_PREFIX}%",),
        ).fetchone()
        total: int = row[0]
        unresolved: int = row[1]
        resolved = total - unresolved
        ratio = unresolved / total if total else 0.0
        rows = self._conn.execute(
            """SELECT target, COUNT(*) AS cnt FROM edges
               WHERE target LIKE ? GROUP BY target ORDER BY cnt DESC LIMIT 10""",
            (f"{RAW_CALL_PREFIX}%",),
        ).fetchall()
        top = [(row["target"], int(row["cnt"])) for row in rows]
        return EdgeStats(
            total=total,
            resolved=resolved,
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
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
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
