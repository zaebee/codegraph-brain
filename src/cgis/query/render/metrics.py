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

from collections.abc import Sequence
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


def _as_terms(terms: Sequence[str]) -> list[str]:
    """Normalise a filter argument into a list of non-blank, trimmed terms.

    A bare string is one term, not a sequence of one-character terms: `Sequence`
    admits `str`, so `dict.fromkeys("domains.res")` iterated characters and built
    eleven single-letter clauses. Over the MCP wire FastMCP's schema catches the
    shape, but a direct Python caller — which is how the MCP tools are invoked in
    tests — got an empty report from a scope, and a stray `'d'` clause that a
    node named `d.foo` would match.

    Trimming for the same reason `find_orphan_classes` trims its `prefix`: a
    copy-pasted `" domains.reservation"` otherwise passes the blank check and
    then matches nothing.
    """
    if isinstance(terms, str):
        terms = [terms]
    # dict.fromkeys dedupes while preserving order.
    return [t for t in dict.fromkeys(term.strip() for term in terms) if t]


def _segment_exclusion(id_expr: str, segments: Sequence[str]) -> tuple[str, list[str]]:
    """Build a WHERE fragment excluding FQNs that contain any of ``segments``.

    A segment matches a whole dot-delimited component **anywhere** in the id, so
    ``tests`` drops both ``tests.utils.x`` and ``domains.resv.tests.test_x`` while
    keeping ``domains.testservice`` (substring, not a segment). Returns
    ``("", [])`` when ``segments`` is empty. The fragment uses ``?`` placeholders
    (the segments ride in as query parameters, never string-interpolated) and
    ``LIKE … ESCAPE '\\'`` with ``%``/``_``/``\\`` escaped, so a segment is matched
    literally and the path stays injection-safe.
    """
    terms = _as_terms(segments)
    if not terms:  # reachable empty case (default ()) — and guards a None caller
        return "", []
    clauses: list[str] = []
    params: list[str] = []
    for seg in terms:
        escaped = seg.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"('.' || {id_expr} || '.') NOT LIKE ? ESCAPE '\\'")
        params.append(f"%.{escaped}.%")
    return " AND " + " AND ".join(clauses), params


def _scope_restriction(id_expr: str, prefixes: Sequence[str]) -> tuple[str, list[str]]:
    """Build a WHERE fragment keeping only FQNs under one of ``prefixes``.

    The complement of :func:`_segment_exclusion`, and deliberately a different
    match: a scope is a *subtree root*, so it is anchored at the start and cut on
    a dot boundary — ``domains.reservation`` keeps ``domains.reservation`` itself
    and everything under ``domains.reservation.``, and rejects the sibling
    ``domains.reservation_archive``. Several prefixes are a union, so repeating
    the flag widens the subtree rather than intersecting it. Returns ``("", [])``
    when ``prefixes`` is empty, which is the whole-graph default. Escaping and
    parameterization match ``_segment_exclusion``: the prefixes ride in as query
    parameters and ``%``/``_``/``\\`` are escaped, so ``a_b`` is a literal
    underscore rather than LIKE's single-character wildcard.
    """
    terms = _as_terms(prefixes)
    if not terms:
        return "", []
    clauses: list[str] = []
    params: list[str] = []
    for prefix in terms:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(f"({id_expr} = ? OR {id_expr} LIKE ? ESCAPE '\\')")
        params.append(prefix)
        params.append(f"{escaped}.%")
    return " AND (" + " OR ".join(clauses) + ")", params


def _coupling_query(filter_sql: str) -> str:
    """Coupling query with optional exclusion/scope fragments on the ranked node list.

    The fragments restrict which nodes are *ranked*; the ``incoming``/``outgoing``
    CTEs above stay whole-graph on purpose, so a scoped ranking still counts
    callers from outside the scope (#239).
    """
    return f"""
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
  AND n.file_path != '{VIRTUAL_FILE_PATH}'{filter_sql}
ORDER BY (COALESCE(i.in_deg, 0) + COALESCE(o.out_deg, 0)) DESC, n.id
LIMIT ?
"""


def _god_class_query(filter_sql: str) -> str:
    """God-class query with optional exclusion/scope fragments on the class id."""
    return f"""
SELECT e.source AS node_id, COUNT(*) AS declares
FROM edges e
JOIN nodes n ON e.source = n.id
WHERE e.type = 'DECLARES' AND n.type = 'CLASS'{filter_sql}
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

    def get_coupling_metrics(
        self, limit: int = 10, exclude: Sequence[str] = (), scope: Sequence[str] = ()
    ) -> list[NodeMetric]:
        """Top INTERNAL functions/methods by total coupling (fan-in + fan-out).

        High in-degree marks a critical bottleneck (many callers); high out-degree
        marks an over-orchestrating or complex unit. External/stdlib and
        unresolved ``raw_call:`` targets are excluded so ``len``/``print`` noise
        never tops the list. ``exclude`` drops any node whose FQN contains one of
        the given dot-segments (e.g. ``["tests"]``) from the ranked list; ``scope``
        keeps only nodes under one of the given dot-prefixes.

        Both restrict the *ranking*, not the counting: in-degree still counts
        callers from outside the scope, because the ripple into a domain is the
        signal a per-domain review is after (#239).
        """
        where, params = _segment_exclusion("n.id", exclude)
        scope_sql, scope_params = _scope_restriction("n.id", scope)
        rows = self.conn.execute(
            _coupling_query(where + scope_sql), [*params, *scope_params, limit]
        ).fetchall()
        return self._rows_to_metrics(rows)

    def get_god_classes(
        self, limit: int = 5, exclude: Sequence[str] = (), scope: Sequence[str] = ()
    ) -> list[NodeMetric]:
        """Top classes by declared-member count (DECLARES fan-out).

        ``out_degree`` carries the number of methods/attributes the class
        declares — a high value is the classic God-object smell. ``exclude`` drops
        classes whose FQN contains one of the given dot-segments; ``scope`` keeps
        only classes under one of the given dot-prefixes.
        """
        where, params = _segment_exclusion("n.id", exclude)
        scope_sql, scope_params = _scope_restriction("n.id", scope)
        rows = self.conn.execute(
            _god_class_query(where + scope_sql), [*params, *scope_params, limit]
        ).fetchall()
        return [
            NodeMetric(
                node_id=str(node_id),
                node_type=NodeType.CLASS.value,
                in_degree=0,
                out_degree=int(declares),
            )
            for node_id, declares in rows
        ]

    def get_pagerank(
        self, limit: int = 10, exclude: Sequence[str] = (), scope: Sequence[str] = ()
    ) -> list[NodeMetric]:
        """Top INTERNAL nodes by PageRank over the internal CALLS graph.

        PageRank weights a node by the *transitive* importance of what reaches it,
        not just direct in-degree, so a function called by other heavily-used
        functions ranks above one called by many trivial leaves. External/stdlib
        and resolver virtual pseudo-nodes are excluded.

        Unlike the coupling metric (FUNCTION/METHOD only), the PageRank universe
        also includes CLASS nodes — a constructor call is a CALLS edge to the
        class, so a heavily-instantiated class (e.g. a core data model) is
        legitimately central. Uncalled classes simply rest at the floor rank.

        ``exclude`` removes any node whose FQN contains one of the given
        dot-segments from the PageRank universe entirely (so excluded nodes leave
        the propagation graph, not just the final ranking).

        ``scope`` is the other semantics on purpose: it filters the *rows*, and
        rank still propagates over the whole graph. A scoped run therefore answers
        "how central is this subtree's code **globally**", and an in-scope node
        scores identically with and without the scope. Restricting the universe
        instead would answer "what is central *within* the subtree" — a different
        question, and one ``--exclude`` already provides the machinery for (#239).

        Each row also carries its ``in_degree``/``out_degree`` **within the same
        internal, exclude-applied CALLS graph PageRank ran on** (#237) — so a
        high rank paired with ``in_degree == 0`` is legible as a dangling-mass
        artifact (a leaf sink that accrued redistributed rank, not a real hub)
        rather than a genuine centrality signal.
        """
        conn = self.conn
        where, params = _segment_exclusion("id", exclude)
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_nodes AS "
            f"SELECT id FROM nodes WHERE {_PAGERANK_INTERNAL}{where}",
            params,
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
            "CREATE OR REPLACE TEMP TABLE pr_in AS "
            "SELECT dst, COUNT(*) AS deg FROM pr_edges GROUP BY dst"
        )
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE pr_rank AS SELECT id, 1.0 / ? AS r FROM pr_nodes", [n]
        )
        base = (1.0 - _PAGERANK_DAMPING) / n
        for _ in range(_PAGERANK_ITERATIONS):
            conn.execute(_PAGERANK_STEP, [base, _PAGERANK_DAMPING, n])
            conn.execute("CREATE OR REPLACE TEMP TABLE pr_rank AS SELECT * FROM pr_next")
        scope_sql, scope_params = _scope_restriction("r.id", scope)
        rows = conn.execute(
            "SELECT r.id, nd.type, r.r, "
            "COALESCE(pin.deg, 0) AS in_deg, COALESCE(pout.deg, 0) AS out_deg "
            "FROM pr_rank r JOIN nodes nd ON r.id = nd.id "
            "LEFT JOIN pr_in pin ON r.id = pin.dst "
            "LEFT JOIN pr_out pout ON r.id = pout.src "
            f"WHERE TRUE{scope_sql} "
            "ORDER BY r.r DESC, r.id LIMIT ?",
            [*scope_params, limit],
        ).fetchall()
        return [
            NodeMetric(
                node_id=str(i),
                node_type=str(t),
                in_degree=int(in_deg),
                out_degree=int(out_deg),
                page_rank=float(r),
            )
            for i, t, r, in_deg, out_deg in rows
        ]

    def architecture_report(
        self,
        bottleneck_limit: int = 10,
        god_limit: int = 5,
        critical_limit: int = 10,
        exclude: Sequence[str] = (),
        scope: Sequence[str] = (),
    ) -> ArchitectureReport:
        """Bundle the coupling bottlenecks, God classes, and PageRank-critical nodes.

        ``exclude`` and ``scope`` are threaded into all three sections: ``exclude``
        drops any node whose FQN contains one of the given dot-segments (e.g.
        ``["tests"]``), ``scope`` keeps only nodes under one of the given
        dot-prefixes. They compose, so ``scope=["domains.reservation"]`` with
        ``exclude=["tests"]`` is one domain minus its own test scaffolding.
        """
        return ArchitectureReport(
            bottlenecks=self.get_coupling_metrics(bottleneck_limit, exclude, scope),
            god_classes=self.get_god_classes(god_limit, exclude, scope),
            critical=self.get_pagerank(critical_limit, exclude, scope),
        )
