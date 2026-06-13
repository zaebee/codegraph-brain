"""DuckDB analytical layer — whole-graph architectural metrics (#16).

SQLite is great for the point reads/writes of ingestion and BFS traversal, but
slow for full-graph aggregations (degree, coupling, God-object detection). DuckDB
is an embedded OLAP engine that *attaches* to the existing ``graph.db`` SQLite
file with zero copy and runs vectorized queries without loading the graph into
Python — so it scales to large monorepos where a NetworkX-in-RAM approach would
OOM.

DuckDB is an **optional** dependency (``pip install codegraph-brain[analytics]``):
importing this module is fine without it, but constructing :class:`DuckDBAnalyzer`
raises a clear error so the CLI can degrade gracefully.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from cgis.core.models import VIRTUAL_FILE_PATH, NodeType

# Optional dependency. Annotated ``Any`` (not ``Module | None``) so the import reads
# the same — and ``duckdb.connect(...)`` type-checks — whether or not duckdb is
# installed in the type-checking environment.
duckdb: Any
try:
    import duckdb
except ImportError:  # pragma: no cover - exercised only without the optional extra
    duckdb = None

_DUCKDB_MISSING = (
    "DuckDB is required for analytical metrics but is not installed. "
    "Install it with: pip install 'codegraph-brain[analytics]'  (or: uv add duckdb)"
)

_COUPLING_QUERY = f"""
WITH incoming AS (
    SELECT target AS node_id, COUNT(*) AS in_deg
    FROM edges WHERE type = 'CALLS' GROUP BY target
),
outgoing AS (
    SELECT e.source AS node_id, COUNT(*) AS out_deg
    FROM edges e
    JOIN nodes t ON e.target = t.id
    WHERE e.type = 'CALLS'
      AND t.namespace = 'INTERNAL'
      AND t.file_path != '{VIRTUAL_FILE_PATH}'
    GROUP BY e.source
)
SELECT
    n.id,
    n.type,
    COALESCE(i.in_deg, 0) AS in_degree,
    COALESCE(o.out_deg, 0) AS out_degree
FROM nodes n
LEFT JOIN incoming i ON n.id = i.node_id
LEFT JOIN outgoing o ON n.id = o.node_id
WHERE n.namespace = 'INTERNAL'
  AND n.type IN ('FUNCTION', 'METHOD')
  AND n.file_path != '{VIRTUAL_FILE_PATH}'
ORDER BY (COALESCE(i.in_deg, 0) + COALESCE(o.out_deg, 0)) DESC, n.id
LIMIT ?
"""

_GOD_CLASS_QUERY = """
SELECT e.source AS node_id, COUNT(*) AS declares
FROM edges e
JOIN nodes n ON e.source = n.id
WHERE e.type = 'DECLARES' AND n.type = 'CLASS'
GROUP BY e.source
ORDER BY declares DESC, e.source
LIMIT ?
"""

# Vectorized PageRank: 20 fixed iterations of the standard damped formula, run as
# DuckDB temp-table joins (Python only drives the loop — the data never leaves the
# in-memory DuckDB). Dangling nodes (no outgoing internal CALLS) redistribute their
# mass uniformly so rank isn't silently lost on a graph full of leaf functions.
_PAGERANK_DAMPING = 0.85
_PAGERANK_ITERATIONS = 20
_PAGERANK_INTERNAL = (
    f"namespace = 'INTERNAL' AND file_path != '{VIRTUAL_FILE_PATH}' "
    "AND type IN ('FUNCTION', 'METHOD', 'CLASS')"
)
_PAGERANK_STEP = """
CREATE OR REPLACE TEMP TABLE pr_next AS
WITH dangling AS (
    SELECT COALESCE(SUM(r.r), 0.0) AS mass
    FROM pr_rank r
    WHERE r.id NOT IN (SELECT src FROM pr_out)
),
inflow AS (
    SELECT e.dst AS id, SUM(r.r / o.deg) AS s
    FROM pr_edges e
    JOIN pr_rank r ON e.src = r.id
    JOIN pr_out o ON e.src = o.src
    GROUP BY e.dst
)
SELECT nd.id AS id,
       ? + ? * (COALESCE(i.s, 0.0) + (SELECT mass FROM dangling) / ?) AS r
FROM pr_nodes nd
LEFT JOIN inflow i ON nd.id = i.id
"""


class NodeMetric(BaseModel):
    """Per-node architectural metric: fan-in (coupling) and fan-out (complexity)."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: str
    in_degree: int
    out_degree: int
    page_rank: float = 0.0


class ArchitectureReport(BaseModel):
    """Whole-graph summary an agent or human can read to spot hotspots."""

    model_config = ConfigDict(frozen=True)

    bottlenecks: list[NodeMetric]
    god_classes: list[NodeMetric]
    critical: list[NodeMetric] = []


class DuckDBAnalyzer:
    """Run vectorized architectural metrics over a SQLite graph via DuckDB.

    Attaches read-only to the SQLite file (zero copy) so the live graph is never
    mutated and ``database is locked`` errors are avoided. Use as a context
    manager so the DuckDB connection is always closed.
    """

    def __init__(self, sqlite_db_path: str) -> None:
        """Open an in-memory DuckDB and attach ``sqlite_db_path`` read-only.

        Raises ``RuntimeError`` if the optional duckdb dependency is missing and
        ``FileNotFoundError`` if the database file does not exist.
        """
        if duckdb is None:
            raise RuntimeError(_DUCKDB_MISSING)
        if not Path(sqlite_db_path).is_file():
            msg = f"Database not found: {sqlite_db_path}. Run `cgis ingest` first."
            raise FileNotFoundError(msg)
        self.conn = duckdb.connect(":memory:")
        try:
            self.conn.execute("INSTALL sqlite;")
            self.conn.execute("LOAD sqlite;")
            # The path can't be a bound parameter in ATTACH; single-quote-escape it
            # (and the is_file check above) keeps the literal injection-safe.
            safe_path = sqlite_db_path.replace("'", "''")
            self.conn.execute(f"ATTACH '{safe_path}' AS gdb (TYPE SQLITE, READ_ONLY);")
            self.conn.execute("USE gdb;")
        except Exception:
            # INSTALL/LOAD/ATTACH can fail (offline extension fetch, non-SQLite file).
            # Close the just-opened connection so it never leaks past a failed __init__.
            self.conn.close()
            raise

    def __enter__(self) -> "DuckDBAnalyzer":
        """Enter the context manager, returning this analyzer."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the DuckDB connection on context exit."""
        self.close()

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self.conn.close()

    def _rows_to_metrics(self, rows: list[tuple[Any, ...]]) -> list[NodeMetric]:
        """Map ``(id, type, in_degree, out_degree)`` rows to NodeMetric models."""
        return [
            NodeMetric(
                node_id=str(node_id),
                node_type=str(node_type),
                in_degree=int(in_degree),
                out_degree=int(out_degree),
            )
            for node_id, node_type, in_degree, out_degree in rows
        ]

    def get_coupling_metrics(self, limit: int = 10) -> list[NodeMetric]:
        """Top INTERNAL functions/methods by total coupling (fan-in + fan-out).

        High in-degree marks a critical bottleneck (many callers); high out-degree
        marks an over-orchestrating or complex unit. External/stdlib and
        unresolved ``raw_call:`` targets are excluded so ``len``/``print`` noise
        never tops the list.
        """
        rows = self.conn.execute(_COUPLING_QUERY, [limit]).fetchall()
        return self._rows_to_metrics(rows)

    def get_god_classes(self, limit: int = 5) -> list[NodeMetric]:
        """Top classes by declared-member count (DECLARES fan-out).

        ``out_degree`` carries the number of methods/attributes the class
        declares — a high value is the classic God-object smell.
        """
        rows = self.conn.execute(_GOD_CLASS_QUERY, [limit]).fetchall()
        return [
            NodeMetric(
                node_id=str(node_id),
                node_type=NodeType.CLASS.value,
                in_degree=0,
                out_degree=int(declares),
            )
            for node_id, declares in rows
        ]

    def get_pagerank(self, limit: int = 10) -> list[NodeMetric]:
        """Top INTERNAL nodes by PageRank over the internal CALLS graph.

        PageRank weights a node by the *transitive* importance of what reaches it,
        not just direct in-degree, so a function called by other heavily-used
        functions ranks above one called by many trivial leaves. External/stdlib
        and resolver virtual pseudo-nodes are excluded.

        Unlike the coupling metric (FUNCTION/METHOD only), the PageRank universe
        also includes CLASS nodes — a constructor call is a CALLS edge to the
        class, so a heavily-instantiated class (e.g. a core data model) is
        legitimately central. Uncalled classes simply rest at the floor rank.
        """
        conn = self.conn
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_nodes AS "
            f"SELECT id FROM nodes WHERE {_PAGERANK_INTERNAL}"
        )
        n = int(conn.execute("SELECT COUNT(*) FROM pr_nodes").fetchone()[0])
        if n == 0:
            return []
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_edges AS "
            "SELECT e.source AS src, e.target AS dst FROM edges e "
            "JOIN pr_nodes s ON e.source = s.id JOIN pr_nodes d ON e.target = d.id "
            "WHERE e.type = 'CALLS'"
        )
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_out AS "
            "SELECT src, COUNT(*) AS deg FROM pr_edges GROUP BY src"
        )
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_rank AS SELECT id, 1.0 / ? AS r FROM pr_nodes", [n]
        )
        base = (1.0 - _PAGERANK_DAMPING) / n
        for _ in range(_PAGERANK_ITERATIONS):
            conn.execute(_PAGERANK_STEP, [base, _PAGERANK_DAMPING, n])
            conn.execute("CREATE OR REPLACE TEMP TABLE pr_rank AS SELECT * FROM pr_next")
        rows = conn.execute(
            "SELECT r.id, nd.type, r.r FROM pr_rank r JOIN nodes nd ON r.id = nd.id "
            "ORDER BY r.r DESC, r.id LIMIT ?",
            [limit],
        ).fetchall()
        return [
            NodeMetric(
                node_id=str(i), node_type=str(t), in_degree=0, out_degree=0, page_rank=float(r)
            )
            for i, t, r in rows
        ]

    def architecture_report(
        self, bottleneck_limit: int = 10, god_limit: int = 5, critical_limit: int = 10
    ) -> ArchitectureReport:
        """Bundle the coupling bottlenecks, God classes, and PageRank-critical nodes."""
        return ArchitectureReport(
            bottlenecks=self.get_coupling_metrics(bottleneck_limit),
            god_classes=self.get_god_classes(god_limit),
            critical=self.get_pagerank(critical_limit),
        )
