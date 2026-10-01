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

import json
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


def _as_terms(terms: Sequence[str] | None) -> list[str]:
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

    `None` means "no filter", which `_segment_exclusion`'s own `if not segments`
    used to cover before this centralised it. No shipped caller passes it — the
    MCP tools do `exclude or []` — but `DuckDBAnalyzer` is public, and a direct
    caller is the same audience the bare-string guard above is for.
    """
    if terms is None:
        return []
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


#: What `cgis validate` counts as unresolved, in DuckDB: a target with no node, an
#: UNKNOWN one, or a surviving `raw_call:`. Kept to the same three cases as
#: `SQLiteStore.get_edge_stats` so the two commands can never report different
#: numbers for one graph (#451). `starts_with` rather than LIKE, whose `_` in
#: `raw_call:` would be a wildcard.
_RESOLUTION_QUERY = """
SELECT
    COUNT(*) AS total,
    COUNT(CASE WHEN n.namespace IS NULL OR n.namespace = 'UNKNOWN'
               OR starts_with(e.target, 'raw_call:') THEN 1 END) AS unresolved
FROM edges e
LEFT JOIN nodes n ON e.target = n.id
WHERE TRUE{filter_sql}
"""


def _file_coupling_query(filter_sql: str) -> str:
    """Per-file afferent/efferent coupling, ranked; fragments filter the ranked files only.

    A dependency is an IMPORTS or CALLS edge between internal nodes of two
    different files — the same rule `build_file_graph` uses for the package
    cohesion graph (#242), so the two views agree on what links two files. Each
    pair of files counts once however many edges join them: Ca and Ce count
    *files*, as Martin's metrics count packages, not calls.

    As with node coupling, the ``filter_sql`` fragments restrict which files are
    ranked while the ``dep`` CTE stays whole-graph, so a scoped file still counts
    dependents from outside the scope (#239).

    One row per file comes from ranking FILE nodes only — an extractor emits
    exactly one per file, its id derived from the path. A MODULE node stands for
    a directory or a domain (`query/engine.py`, `quotient.py`), not a file, so
    listing it here would both mislabel it and, sharing a `file_path` with a
    FILE node, repeat that file (#520 review).
    """
    return f"""
WITH dep AS (
    SELECT DISTINCT s.file_path AS src_file, t.file_path AS dst_file
    FROM edges e
    JOIN nodes s ON e.source = s.id
    JOIN nodes t ON e.target = t.id
    WHERE e.type IN ('IMPORTS', 'CALLS')
      AND s.namespace = 'INTERNAL' AND t.namespace = 'INTERNAL'
      AND s.file_path != '{VIRTUAL_FILE_PATH}' AND t.file_path != '{VIRTUAL_FILE_PATH}'
      AND s.file_path != t.file_path
),
ca AS (SELECT dst_file AS file_path, COUNT(*) AS n FROM dep GROUP BY dst_file),
ce AS (SELECT src_file AS file_path, COUNT(*) AS n FROM dep GROUP BY src_file)
SELECT
    f.id,
    f.file_path,
    COALESCE(ca.n, 0) AS afferent,
    COALESCE(ce.n, 0) AS efferent
FROM nodes f
LEFT JOIN ca ON f.file_path = ca.file_path
LEFT JOIN ce ON f.file_path = ce.file_path
WHERE f.type = 'FILE'
  AND f.namespace = 'INTERNAL'
  AND f.file_path != '{VIRTUAL_FILE_PATH}'{filter_sql}
ORDER BY (COALESCE(ca.n, 0) + COALESCE(ce.n, 0)) DESC, f.id
LIMIT ?
"""


#: A class's methods with their metadata, by DECLARES — the edge the extractor
#: emits from a class to each method it defines (#451).
_CLASS_METHODS_QUERY = """
SELECT c.id, m.id, m.name, m.metadata
FROM edges e
JOIN nodes c ON e.source = c.id
JOIN nodes m ON e.target = m.id
WHERE e.type = 'DECLARES' AND c.type = 'CLASS' AND m.type = 'METHOD'
  AND c.namespace = 'INTERNAL'{filter_sql}
"""

#: CALLS edges between two methods of one class, joined in DuckDB so only those
#: leave the database rather than every call in the graph (#521 review).
_INTRA_CLASS_CALLS_QUERY = """
SELECT e.source, e.target
FROM edges e
JOIN edges ds ON ds.target = e.source AND ds.type = 'DECLARES'
JOIN edges dt ON dt.target = e.target AND dt.type = 'DECLARES'
WHERE e.type = 'CALLS' AND ds.source = dt.source
"""

#: Decorators that take a method out of LCOM4: it does not work on the instance.
_NOT_INSTANCE_DECORATORS = frozenset({"staticmethod", "classmethod"})


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


class ResolutionMetric(BaseModel):
    """How many edges the resolver could not place, beside the rankings (#451).

    The rankings above it are only as good as the edges under them: a module whose
    calls are mostly unresolved looks uncoupled because its edges point nowhere.
    """

    model_config = ConfigDict(frozen=True)

    total_edges: int
    unresolved_edges: int
    unresolved_ratio: float


class FileCoupling(BaseModel):
    """Martin's coupling for one file: who depends on it, what it depends on (#451).

    Node-level coupling ranks functions; this is the unit a refactor moves. A
    file with high Ca and low Ce is depended on and depends on little — stable,
    expensive to change; the reverse is volatile and cheap to change.
    """

    model_config = ConfigDict(frozen=True)

    #: The file's module FQN, as `--scope` and `--exclude` match it.
    module: str
    file_path: str
    #: Ca: how many other files depend on this one.
    afferent: int
    #: Ce: how many other files this one depends on.
    efferent: int
    #: I = Ce / (Ca + Ce), from 0 (stable) to 1 (unstable); None for a file
    #: that links to no other, where the ratio is undefined rather than zero.
    instability: float | None


class ClassCohesion(BaseModel):
    """LCOM4 for one class: how many unrelated groups its methods fall into (#451).

    1 is a cohesive class. 2 or more means the methods split into groups that
    share no field and call nothing across — candidates to be separate classes.
    """

    model_config = ConfigDict(frozen=True)

    class_id: str
    #: The instance methods counted — dunders, abstract, static and class
    #: methods are left out (see `class_cohesion_metrics`).
    methods: int
    lcom4: int


class ArchitectureReport(BaseModel):
    """Whole-graph summary an agent or human can read to spot hotspots."""

    model_config = ConfigDict(frozen=True)

    bottlenecks: list[NodeMetric]
    god_classes: list[NodeMetric]
    critical: list[NodeMetric] = []
    file_coupling: list[FileCoupling] = []
    class_cohesion: list[ClassCohesion] = []
    resolution: ResolutionMetric


def resolution_metric(
    analyzer: "DuckDBAnalyzer", exclude: Sequence[str] = (), scope: Sequence[str] = ()
) -> ResolutionMetric:
    """The share of edges the resolver left unplaced, by `cgis validate`'s rule (#451).

    Unfiltered it is the same number `cgis validate` reports. ``exclude`` and
    ``scope`` filter by the edge's *source*: a scoped run answers "of the edges
    this subtree's code emits, how many point nowhere", which is the caveat its
    own rankings carry. Filtering by target instead would drop exactly the
    unresolved edges, whose targets have no FQN in the subtree.

    A function over the connection rather than a `DuckDBAnalyzer` method: the
    class is at the size the god-object baseline allows.
    """
    where, params = _segment_exclusion("e.source", exclude)
    scope_sql, scope_params = _scope_restriction("e.source", scope)
    row = analyzer.conn.execute(
        _RESOLUTION_QUERY.format(filter_sql=where + scope_sql), [*params, *scope_params]
    ).fetchone()
    total, unresolved = (int(row[0]), int(row[1])) if row else (0, 0)
    return ResolutionMetric(
        total_edges=total,
        unresolved_edges=unresolved,
        unresolved_ratio=unresolved / total if total else 0.0,
    )


def file_coupling_metrics(
    analyzer: "DuckDBAnalyzer",
    limit: int = 10,
    exclude: Sequence[str] = (),
    scope: Sequence[str] = (),
) -> list[FileCoupling]:
    """Top internal files by Ca + Ce, with their instability (#451).

    ``exclude`` and ``scope`` match the file's module FQN and restrict the
    ranking only; Ca and Ce still count every file in the graph. A module
    function for the same reason as `resolution_metric`: `DuckDBAnalyzer` is at
    the god-object baseline.
    """
    where, params = _segment_exclusion("f.id", exclude)
    scope_sql, scope_params = _scope_restriction("f.id", scope)
    rows = analyzer.conn.execute(
        _file_coupling_query(where + scope_sql), [*params, *scope_params, limit]
    ).fetchall()
    return [
        FileCoupling(
            module=str(module),
            file_path=str(path),
            afferent=int(ca),
            efferent=int(ce),
            instability=int(ce) / (int(ca) + int(ce)) if int(ca) + int(ce) else None,
        )
        for module, path, ca, ce in rows
    ]


def _counts_for_lcom4(name: str, metadata: dict[str, Any]) -> bool:
    """Whether a method takes part in LCOM4: an instance method of the class's own behaviour.

    Dunders are out, `__init__` first: it assigns every field, so counting it
    glues nearly every class into one component and LCOM4 reads 1 regardless.
    Abstract methods have no body to share a field with, and static and class
    methods do not work on the instance; each would count as a group of its own.
    """
    if name.startswith("__") and name.endswith("__"):
        return False
    if metadata.get("is_abstract"):
        return False
    decorators = metadata.get("decorators") or []
    return not any(str(d).rsplit(".", 1)[-1] in _NOT_INSTANCE_DECORATORS for d in decorators)


def _lcom4(methods: dict[str, tuple[str, list[str]]], calls: set[tuple[str, str]]) -> int:
    """Connected components over methods linked by a shared attribute or a call.

    ``methods`` maps method id to (name, attributes it touches through its
    receiver). A method is also joined to its own name, so `self.flush` used
    anywhere links to the `flush` method — a reference is as much a link as a
    call.
    """
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for method_id, (name, attrs) in methods.items():
        union(method_id, f"attr:{name}")
        for attr in attrs:
            union(method_id, f"attr:{attr}")
    for source, target in calls:
        union(source, target)
    return len({find(method_id) for method_id in methods})


def class_cohesion_metrics(
    analyzer: "DuckDBAnalyzer",
    limit: int = 10,
    exclude: Sequence[str] = (),
    scope: Sequence[str] = (),
) -> list[ClassCohesion]:
    """Internal classes ranked by LCOM4, least cohesive first (#451).

    Methods are linked when they share an attribute of the receiver or one calls
    the other; LCOM4 is the number of groups that leaves. Which methods count is
    `_counts_for_lcom4`. A class with no method left to count has no LCOM4 and
    is not listed.

    A class whose methods carry no ``self_attrs`` comes from a graph built
    before the extractor recorded them; it is skipped rather than reported as
    falling apart, since every method would look like it touched nothing.
    ``exclude`` and ``scope`` match the class FQN. A module function, like the
    other per-report metrics, because `DuckDBAnalyzer` is at the god-object
    baseline.
    """
    where, params = _segment_exclusion("c.id", exclude)
    scope_sql, scope_params = _scope_restriction("c.id", scope)
    rows = analyzer.conn.execute(
        _CLASS_METHODS_QUERY.format(filter_sql=where + scope_sql), [*params, *scope_params]
    ).fetchall()
    by_class: dict[str, dict[str, tuple[str, list[str]]]] = {}
    stale: set[str] = set()
    for class_id, method_id, name, raw in rows:
        metadata = json.loads(raw) if raw else {}
        if "self_attrs" not in metadata:
            stale.add(str(class_id))
            continue
        if _counts_for_lcom4(str(name), metadata):
            attrs = [str(a) for a in metadata["self_attrs"]]
            by_class.setdefault(str(class_id), {})[str(method_id)] = (str(name), attrs)
    owner = {m: c for c, methods in by_class.items() for m in methods}
    calls: dict[str, set[tuple[str, str]]] = {}
    for source, target in analyzer.conn.execute(_INTRA_CLASS_CALLS_QUERY).fetchall():
        cls = owner.get(str(source))
        if cls is not None and owner.get(str(target)) == cls:
            calls.setdefault(cls, set()).add((str(source), str(target)))
    report = [
        ClassCohesion(class_id=c, methods=len(methods), lcom4=_lcom4(methods, calls.get(c, set())))
        for c, methods in by_class.items()
        if c not in stale and methods
    ]
    report.sort(key=lambda r: (-r.lcom4, -r.methods, r.class_id))
    return report[:limit]


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
        file_limit: int = 10,
        cohesion_limit: int = 10,
        exclude: Sequence[str] = (),
        scope: Sequence[str] = (),
    ) -> ArchitectureReport:
        """Bundle node coupling, God classes, PageRank, file coupling, LCOM4 and resolution.

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
            file_coupling=file_coupling_metrics(self, file_limit, exclude, scope),
            class_cohesion=class_cohesion_metrics(self, cohesion_limit, exclude, scope),
            resolution=resolution_metric(self, exclude, scope),
        )
