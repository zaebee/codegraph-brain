"""Implements Sqlite store for code graph."""

import json
import sqlite3

from cgis.core.models import Edge, EdgeType, Node, NodeType


class SQLiteStore:
    """
    Deterministic SQLite Graph Store.
    Manages persistence of Nodes and Edges with high performance indexing.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Establishes connection and initializes database schema."""
        self._conn = sqlite3.connect(self.db_path)
        # Enable WAL mode for better write-ahead performance
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

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

        CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        """
        if self._conn:
            self._conn.executescript(schema)
            self._conn.commit()

    def save_graph(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Persists all nodes and edges inside a single transaction."""
        if not self._conn:
            msg = "Database not connected."
            raise RuntimeError(msg)

        # Clear old data to prevent duplication on re-ingestion
        self._conn.execute("DELETE FROM nodes")
        self._conn.execute("DELETE FROM edges")

        # Insert Nodes
        node_query = """
        INSERT INTO nodes (
            id, type, name, file_path, start_line, end_line, language,
            ontology_class, domains, confidence_score, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        node_rows = [
            (
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
            for n in nodes
        ]
        self._conn.executemany(node_query, node_rows)

        # Insert Edges
        edge_query = """
        INSERT INTO edges (
            id, source, target, type, weight, confidence, context, file_path, line_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        edge_rows = [
            (
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
            for e in edges
        ]
        self._conn.executemany(edge_query, edge_rows)
        self._conn.commit()

    def get_node(self, node_id: str) -> Node | None:
        if not self._conn:
            return None
        cursor = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_node(row)

    def get_outgoing_edges(self, node_id: str) -> list[Edge]:
        if not self._conn:
            return []
        cursor = self._conn.execute("SELECT * FROM edges WHERE source = ?", (node_id,))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def get_incoming_edges(self, node_id: str) -> list[Edge]:
        if not self._conn:
            return []
        cursor = self._conn.execute("SELECT * FROM edges WHERE target = ?", (node_id,))
        return [self._row_to_edge(row) for row in cursor.fetchall()]

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        return Node(
            id=row[0],
            type=NodeType(row[1]),
            name=row[2],
            file_path=row[3],
            start_line=row[4],
            end_line=row[5],
            language=row[6],
            ontology_class=row[7],
            domains=json.loads(row[8]) if row[8] else [],
            confidence_score=row[9],
            metadata=json.loads(row[10]) if row[10] else {},
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        return Edge(
            id=row[0],
            source=row[1],
            target=row[2],
            type=EdgeType(row[3]),
            weight=row[4],
            confidence=row[5],
            context=row[6],
            file_path=row[7],
            line_number=row[8],
        )
