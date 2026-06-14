"""Unit test cases for sqlite storage."""

import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.storage.sqlite_store import SQLiteStore


@pytest.fixture
def temp_store() -> Generator[SQLiteStore, None, None]:
    """Create temp clean DB in memory for each case."""
    store = SQLiteStore(":memory:")
    store.connect()
    yield store
    store.disconnect()


def _seed_data() -> tuple[list[Node], list[Edge]]:
    nodes = [
        Node(id="A", type=NodeType.FUNCTION, name="A", file_path="f.py", start_line=1, end_line=2),
        Node(id="B", type=NodeType.FUNCTION, name="B", file_path="f.py", start_line=3, end_line=4),
        Node(id="C", type=NodeType.FUNCTION, name="C", file_path="f.py", start_line=5, end_line=6),
    ]
    edges = [
        Edge(id="A->B", source="A", target="B", type=EdgeType.CALLS),
        Edge(id="B->C", source="B", target="C", type=EdgeType.CALLS),
    ]
    return nodes, edges


def test_save_and_load_graph(temp_store: SQLiteStore) -> None:
    """Check that edges and nodes save and load without corruption."""
    node = Node(
        id="pkg.mod:func_a",
        type=NodeType.FUNCTION,
        name="func_a",
        file_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        language="python",
        metadata={"complexity": "O(1)"},
    )
    edge = Edge(id="edge_1", source="pkg.mod:func_a", target="pkg.mod:func_b", type=EdgeType.CALLS)

    temp_store.save_graph([node], [edge])

    # Load back
    loaded_node = temp_store.get_node("pkg.mod:func_a")
    assert loaded_node is not None
    assert loaded_node.name == "func_a"
    assert loaded_node.metadata == {"complexity": "O(1)"}

    loaded_edges = temp_store.get_outgoing_edges("pkg.mod:func_a")
    assert len(loaded_edges) == 1
    assert loaded_edges[0].target == "pkg.mod:func_b"


def test_query_engine_impact_analysis(temp_store: SQLiteStore) -> None:
    """
    Check Impact Analysis (trace to up stack).
    If C changes -> this affect to B -> affect to A.
    """
    nodes, edges = _seed_data()
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)

    # Run analysis changes impact in C
    impact_nodes, impact_edges = query_engine.get_impact_graph("C", max_depth=3)

    # Impact should affect B and A, also contains edges A->B and B->C
    impact_ids = {n.id for n in impact_nodes}
    assert "C" in impact_ids
    assert "B" in impact_ids
    assert "A" in impact_ids
    assert len(impact_edges) == 2


def test_query_engine_flow_tracing(temp_store: SQLiteStore) -> None:
    """
    Check Flow Tracing (Trace chain of calls down).
    A calls B, who calls C.
    """
    nodes, edges = _seed_data()
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)

    # trace run from A
    flow_nodes, flow_edges = query_engine.get_flow_graph("A", max_depth=3)

    flow_ids = {n.id for n in flow_nodes}
    assert "A" in flow_ids
    assert "B" in flow_ids
    assert "C" in flow_ids
    assert len(flow_edges) == 2


def _make_node(node_id: str) -> Node:
    return Node(
        id=node_id, type=NodeType.FUNCTION, name=node_id, file_path="f.py", start_line=1, end_line=2
    )


def _make_edge(source: str, target: str) -> Edge:
    return Edge(id=f"{source}->{target}", source=source, target=target, type=EdgeType.CALLS)


def test_flow_graph_cycle_does_not_loop(temp_store: SQLiteStore) -> None:
    """A -> B -> A cycle must terminate without visiting nodes more than once."""
    nodes = [_make_node(n) for n in ("A", "B")]
    edges = [_make_edge("A", "B"), _make_edge("B", "A")]
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)
    flow_nodes, flow_edges = query_engine.get_flow_graph("A", max_depth=10)

    flow_ids = {n.id for n in flow_nodes}
    assert flow_ids == {"A", "B"}
    assert len(flow_edges) == 2


def test_impact_graph_cycle_does_not_loop(temp_store: SQLiteStore) -> None:
    """A -> B -> A cycle: impact from B must terminate without visiting nodes more than once."""
    nodes = [_make_node(n) for n in ("A", "B")]
    edges = [_make_edge("A", "B"), _make_edge("B", "A")]
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)
    impact_nodes, impact_edges = query_engine.get_impact_graph("B", max_depth=10)

    impact_ids = {n.id for n in impact_nodes}
    assert impact_ids == {"A", "B"}
    assert len(impact_edges) == 2


def test_flow_graph_diamond_no_duplicate_visits(temp_store: SQLiteStore) -> None:
    """Diamond A->B->D, A->C->D: D must appear exactly once in results."""
    nodes = [_make_node(n) for n in ("A", "B", "C", "D")]
    edges = [_make_edge("A", "B"), _make_edge("A", "C"), _make_edge("B", "D"), _make_edge("C", "D")]
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)
    flow_nodes, flow_edges = query_engine.get_flow_graph("A", max_depth=5)

    flow_ids = {n.id for n in flow_nodes}
    assert flow_ids == {"A", "B", "C", "D"}
    # All 4 edges traversed, D not duplicated in node list
    assert len(flow_nodes) == 4
    assert len(flow_edges) == 4


def test_impact_graph_external_node(temp_store: SQLiteStore) -> None:
    """Impact analysis from a node not in the nodes table must still find callers.

    Unresolved symbols (e.g. raw_call:print) have CALLS edges but no node entry.
    The traversal should return the callers, not an empty result.
    """
    nodes = [_make_node("A")]
    edges = [_make_edge("A", "Ext")]
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)
    impact_nodes, impact_edges = query_engine.get_impact_graph("Ext", max_depth=3)

    impact_ids = {n.id for n in impact_nodes}
    assert "A" in impact_ids
    assert "Ext" not in impact_ids  # not in nodes table — silently skipped by get_nodes
    assert len(impact_edges) == 1
    assert impact_edges[0].source == "A"


def test_file_hash_roundtrip(temp_store: SQLiteStore) -> None:
    """upsert_file_hash and get_file_hash persist and retrieve hashes correctly."""
    assert temp_store.get_file_hash("src/mod.py") is None

    temp_store.upsert_file_hash("src/mod.py", "abc123")
    assert temp_store.get_file_hash("src/mod.py") == "abc123"

    # Upsert again with new hash (overwrite)
    temp_store.upsert_file_hash("src/mod.py", "def456")
    assert temp_store.get_file_hash("src/mod.py") == "def456"


def test_delete_file_data_removes_nodes_edges_and_hash(temp_store: SQLiteStore) -> None:
    """delete_file_data removes nodes, edges and the files_state entry for that file."""
    nodes = [
        Node(
            id="mod.func_a",
            type=NodeType.FUNCTION,
            name="func_a",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="mod.func_b",
            type=NodeType.FUNCTION,
            name="func_b",
            file_path="mod.py",
            start_line=3,
            end_line=4,
        ),
    ]
    edges = [
        Edge(
            id="e1",
            source="mod.func_a",
            target="mod.func_b",
            type=EdgeType.CALLS,
            file_path="mod.py",
        )
    ]
    temp_store.save_graph(nodes, edges)
    temp_store.upsert_file_hash("mod.py", "abc")

    temp_store.delete_file_data("mod.py")

    assert temp_store.get_node("mod.func_a") is None
    assert temp_store.get_node("mod.func_b") is None
    assert temp_store.get_outgoing_edges("mod.func_a") == []
    assert temp_store.get_file_hash("mod.py") is None


def test_delete_file_data_removes_edges_without_file_path(temp_store: SQLiteStore) -> None:
    """delete_file_data also removes structural edges whose source belongs to the deleted file."""
    nodes = [
        Node(
            id="mod.func",
            type=NodeType.FUNCTION,
            name="func",
            file_path="mod.py",
            start_line=1,
            end_line=2,
        ),
    ]
    # Edge with file_path=None — simulates a future structural edge
    edges = [
        Edge(
            id="struct_e1",
            source="mod.func",
            target="other.func",
            type=EdgeType.CALLS,
            file_path=None,
        )
    ]
    temp_store.save_graph(nodes, edges)
    temp_store.upsert_file_hash("mod.py", "abc")

    temp_store.delete_file_data("mod.py")

    assert temp_store.get_node("mod.func") is None
    assert temp_store.get_outgoing_edges("mod.func") == []
    assert temp_store.get_file_hash("mod.py") is None


def test_save_incremental_batch_single_transaction(temp_store: SQLiteStore) -> None:
    """save_incremental_batch persists nodes, edges, hashes and removes stale files atomically."""
    # Pre-seed stale file
    stale_node = Node(
        id="old.fn", type=NodeType.FUNCTION, name="fn", file_path="old.py", start_line=1, end_line=2
    )
    temp_store.save_graph([stale_node], [])
    temp_store.upsert_file_hash("old.py", "stale_hash")

    new_node = Node(
        id="new.func",
        type=NodeType.FUNCTION,
        name="func",
        file_path="new.py",
        start_line=1,
        end_line=2,
    )
    new_edge = Edge(
        id="e1", source="new.func", target="raw_call:print", type=EdgeType.CALLS, file_path="new.py"
    )

    temp_store.save_incremental_batch(
        nodes_by_file={"new.py": [new_node]},
        edges_by_file={"new.py": [new_edge]},
        file_hashes={"new.py": "new_hash"},
        stale_files={"old.py"},
    )

    assert temp_store.get_node("new.func") is not None
    assert temp_store.get_file_hash("new.py") == "new_hash"
    assert temp_store.get_node("old.fn") is None
    assert temp_store.get_file_hash("old.py") is None
    assert len(temp_store.get_outgoing_edges("new.func")) == 1


def test_get_nodes_by_file(temp_store: SQLiteStore) -> None:
    """get_nodes_by_file returns only nodes for the requested file."""
    nodes = [
        Node(
            id="a.func",
            type=NodeType.FUNCTION,
            name="func",
            file_path="a.py",
            start_line=1,
            end_line=2,
        ),
        Node(
            id="b.func",
            type=NodeType.FUNCTION,
            name="func",
            file_path="b.py",
            start_line=1,
            end_line=2,
        ),
    ]
    temp_store.save_graph(nodes, [])

    result = temp_store.get_nodes_by_file("a.py")
    assert len(result) == 1
    assert result[0].id == "a.func"


def test_get_all_tracked_files(temp_store: SQLiteStore) -> None:
    """get_all_tracked_files returns the set of all files with a stored hash."""
    temp_store.upsert_file_hash("a.py", "h1")
    temp_store.upsert_file_hash("b.py", "h2")

    tracked = temp_store.get_all_tracked_files()
    assert tracked == {"a.py", "b.py"}


def test_impact_graph_diamond_no_duplicate_visits(temp_store: SQLiteStore) -> None:
    """Diamond A->B->D, A->C->D: impact from D reaches A exactly once."""
    nodes = [_make_node(n) for n in ("A", "B", "C", "D")]
    edges = [_make_edge("A", "B"), _make_edge("A", "C"), _make_edge("B", "D"), _make_edge("C", "D")]
    temp_store.save_graph(nodes, edges)

    query_engine = QueryEngine(temp_store)
    impact_nodes, impact_edges = query_engine.get_impact_graph("D", max_depth=5)

    impact_ids = {n.id for n in impact_nodes}
    assert impact_ids == {"A", "B", "C", "D"}
    assert len(impact_nodes) == 4
    assert len(impact_edges) == 4


# --- Coverage gap tests ---


def test_connect_idempotent() -> None:
    with SQLiteStore(":memory:") as store:
        conn1 = store._conn  # noqa: SLF001
        store.connect()  # second call — must return early without re-creating
        assert store._conn is conn1  # noqa: SLF001


def test_migrate_adds_namespace_column(tmp_path: Path) -> None:
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
            file_path TEXT NOT NULL, start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL, language TEXT NOT NULL,
            ontology_class TEXT, domains TEXT,
            confidence_score REAL NOT NULL, metadata TEXT
        );
        CREATE TABLE edges (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
            type TEXT NOT NULL, weight REAL NOT NULL, confidence REAL NOT NULL,
            context TEXT, file_path TEXT, line_number INTEGER
        );
        CREATE TABLE files_state (file_path TEXT PRIMARY KEY, hash TEXT NOT NULL);
    """)
    conn.commit()
    conn.close()

    with SQLiteStore(db_path) as store:
        assert store._conn is not None, "Database connection is not established"  # noqa: SLF001
        cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(nodes)").fetchall()  # noqa: SLF001
        }

    assert "namespace" in cols


def test_store_methods_raise_when_not_connected() -> None:
    store = SQLiteStore(":memory:")
    # intentionally never call connect()
    sentinel = Node(
        id="x", type=NodeType.FUNCTION, name="x", file_path="f.py", start_line=1, end_line=1
    )
    edge_sentinel = Edge(id="e", source="x", target="y", type=EdgeType.CALLS, confidence=0.5)
    cases = [
        lambda: store.upsert_nodes([sentinel]),
        lambda: store.save_graph([sentinel], [edge_sentinel]),
        lambda: store.get_node("x"),
        lambda: store.get_nodes(["x"]),
        lambda: store.get_outgoing_edges("x"),
        lambda: store.get_incoming_edges("x"),
        lambda: store.get_file_hash("x"),
        lambda: store.upsert_file_hash("x", "hash"),
        lambda: store.delete_file_data("x"),
        lambda: store.save_incremental_batch({}, {}, {}, set()),
        lambda: store.get_nodes_by_file("x"),
        lambda: store.get_structural_subgraph("x"),
        store.get_edge_stats,
        store.get_all_tracked_files,
    ]
    for case in cases:
        with pytest.raises(RuntimeError):
            case()


def test_get_edges_batch_invalid_column_raises(temp_store: SQLiteStore) -> None:
    with pytest.raises(ValueError, match="Invalid column"):
        temp_store._get_edges_batch(["x"], column="bad_col")  # noqa: SLF001


def test_get_edges_batch_empty_list_returns_empty(temp_store: SQLiteStore) -> None:
    assert temp_store.get_outgoing_edges_batch([]) == []
    assert temp_store.get_incoming_edges_batch([]) == []


def test_get_structural_subgraph_unknown_node_returns_empty(temp_store: SQLiteStore) -> None:
    nodes, edges = temp_store.get_structural_subgraph("nonexistent.Node")
    assert nodes == []
    assert edges == []


# --- Issue #58: Query Filtering & Noise Pruning ---


def test_flow_graph_filters_structural_edges(temp_store: SQLiteStore) -> None:
    """allowed_edge_types excludes CONTAINS/DECLARES so structural nodes are not traversed."""
    nodes = [_make_node("parent"), _make_node("child"), _make_node("callee")]
    edges = [
        Edge(id="p->c", source="parent", target="child", type=EdgeType.CONTAINS),
        Edge(id="p->f", source="parent", target="callee", type=EdgeType.CALLS),
    ]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    # Default (no filter): both edges traversed
    all_nodes, all_edges = qe.get_flow_graph("parent", max_depth=2)
    assert {n.id for n in all_nodes} == {"parent", "child", "callee"}
    assert len(all_edges) == 2

    # BEHAVIORAL_EDGE_TYPES: CONTAINS edge skipped → child not reached
    filt_nodes, filt_edges = qe.get_flow_graph(
        "parent", max_depth=2, allowed_edge_types=BEHAVIORAL_EDGE_TYPES
    )
    assert {n.id for n in filt_nodes} == {"parent", "callee"}
    assert len(filt_edges) == 1
    assert filt_edges[0].type == EdgeType.CALLS


def test_flow_graph_filters_by_min_confidence(temp_store: SQLiteStore) -> None:
    """min_confidence drops low-confidence edges (e.g. unresolved raw_call) (#112)."""
    nodes = [_make_node("caller"), _make_node("solid"), _make_node("weak")]
    edges = [
        Edge(id="c->s", source="caller", target="solid", type=EdgeType.CALLS, confidence=1.0),
        Edge(id="c->w", source="caller", target="weak", type=EdgeType.CALLS, confidence=0.1),
    ]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    # No filter: both reached.
    n_all, _ = qe.get_flow_graph("caller", max_depth=2)
    assert {n.id for n in n_all} == {"caller", "solid", "weak"}

    # min_confidence 0.5 prunes the 0.1 edge → weak is unreachable.
    n_f, e_f = qe.get_flow_graph("caller", max_depth=2, min_confidence=0.5)
    assert {n.id for n in n_f} == {"caller", "solid"}
    assert all(e.confidence >= 0.5 for e in e_f)


def test_impact_graph_filters_by_min_confidence(temp_store: SQLiteStore) -> None:
    """min_confidence applies to upstream impact traversal too (#112)."""
    nodes = [_make_node("strong_caller"), _make_node("weak_caller"), _make_node("target")]
    edges = [
        Edge(
            id="s->t", source="strong_caller", target="target", type=EdgeType.CALLS, confidence=1.0
        ),
        Edge(id="w->t", source="weak_caller", target="target", type=EdgeType.CALLS, confidence=0.1),
    ]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    n_f, _ = qe.get_impact_graph("target", max_depth=2, min_confidence=0.5)
    assert {n.id for n in n_f} == {"target", "strong_caller"}


def test_impact_graph_filters_structural_edges(temp_store: SQLiteStore) -> None:
    """allowed_edge_types excludes CONTAINS/DECLARES in upstream impact traversal."""
    nodes = [_make_node("container"), _make_node("caller"), _make_node("target")]
    edges = [
        Edge(id="cont->tgt", source="container", target="target", type=EdgeType.CONTAINS),
        Edge(id="caller->tgt", source="caller", target="target", type=EdgeType.CALLS),
    ]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    filt_nodes, filt_edges = qe.get_impact_graph(
        "target", max_depth=2, allowed_edge_types=BEHAVIORAL_EDGE_TYPES
    )
    node_ids = {n.id for n in filt_nodes}
    assert "caller" in node_ids
    assert "container" not in node_ids
    assert len(filt_edges) == 1


def test_flow_graph_prunes_external_nodes(temp_store: SQLiteStore) -> None:
    """show_external=False removes STDLIB/EXTERNAL nodes and edges that cross the boundary."""
    internal = Node(
        id="mod.fn",
        type=NodeType.FUNCTION,
        name="fn",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        namespace=NodeNamespace.INTERNAL,
    )
    stdlib_node = Node(
        id="os.path.join",
        type=NodeType.FUNCTION,
        name="join",
        file_path=VIRTUAL_FILE_PATH,
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.STDLIB,
    )
    nodes = [internal, stdlib_node]
    edges = [Edge(id="e1", source="mod.fn", target="os.path.join", type=EdgeType.CALLS)]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    # With show_external=True (default): stdlib node present
    all_nodes, all_edges = qe.get_flow_graph("mod.fn", max_depth=2)
    assert "os.path.join" in {n.id for n in all_nodes}
    assert len(all_edges) == 1

    # With show_external=False: stdlib node and its edge pruned
    filt_nodes, filt_edges = qe.get_flow_graph("mod.fn", max_depth=2, show_external=False)
    assert "os.path.join" not in {n.id for n in filt_nodes}
    assert len(filt_edges) == 0


def test_impact_graph_prunes_external_nodes(temp_store: SQLiteStore) -> None:
    """show_external=False prunes external callers from impact traversal."""
    internal = Node(
        id="mod.fn",
        type=NodeType.FUNCTION,
        name="fn",
        file_path="mod.py",
        start_line=1,
        end_line=2,
        namespace=NodeNamespace.INTERNAL,
    )
    external_caller = Node(
        id="pkg.util",
        type=NodeType.FUNCTION,
        name="util",
        file_path=VIRTUAL_FILE_PATH,
        start_line=0,
        end_line=0,
        namespace=NodeNamespace.EXTERNAL,
    )
    nodes = [internal, external_caller]
    edges = [Edge(id="e1", source="pkg.util", target="mod.fn", type=EdgeType.CALLS)]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    filt_nodes, filt_edges = qe.get_impact_graph("mod.fn", max_depth=2, show_external=False)
    assert "pkg.util" not in {n.id for n in filt_nodes}
    assert len(filt_edges) == 0


def test_flow_graph_prunes_disconnected_internal_nodes(temp_store: SQLiteStore) -> None:
    """show_external=False prunes internal nodes only reachable via external hops.

    A(internal) -> B(external) -> C(internal): with show_external=False, C is
    disconnected after B is removed, so it should not appear in the result.
    """
    nodes = [
        Node(
            id="A",
            type=NodeType.FUNCTION,
            name="A",
            file_path="mod.py",
            start_line=1,
            end_line=2,
            namespace=NodeNamespace.INTERNAL,
        ),
        Node(
            id="B",
            type=NodeType.FUNCTION,
            name="B",
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=NodeNamespace.EXTERNAL,
        ),
        Node(
            id="C",
            type=NodeType.FUNCTION,
            name="C",
            file_path="mod.py",
            start_line=3,
            end_line=4,
            namespace=NodeNamespace.INTERNAL,
        ),
    ]
    edges = [
        Edge(id="A->B", source="A", target="B", type=EdgeType.CALLS),
        Edge(id="B->C", source="B", target="C", type=EdgeType.CALLS),
    ]
    temp_store.save_graph(nodes, edges)
    qe = QueryEngine(temp_store)

    filt_nodes, filt_edges = qe.get_flow_graph("A", max_depth=3, show_external=False)
    node_ids = {n.id for n in filt_nodes}
    assert "A" in node_ids
    assert "B" not in node_ids
    assert "C" not in node_ids  # disconnected after external hop removed
    assert len(filt_edges) == 0


# --- find_nodes_by_suffix tests (#145) ---


def _seed_suffix_store(tmp_path: Path, ids: list[str]) -> Path:
    """Seed a SQLite store with FUNCTION nodes for the given FQNs and return the db path."""
    db_path = tmp_path / "suffix.db"
    nodes = [
        Node(
            id=i,
            type=NodeType.FUNCTION,
            name=i.rsplit(".", 1)[-1],
            file_path="f.py",
            start_line=1,
            end_line=2,
        )
        for i in ids
    ]
    with SQLiteStore(str(db_path)) as store:
        store.save_graph(nodes, [])
    return db_path


def test_find_nodes_by_suffix_pure_suffix_semantics(tmp_path: Path) -> None:
    """The id itself is NOT its own dot-boundary suffix — only longer FQNs match.

    Exact-match policy lives in resolve_fqn (query layer), not here.
    Seeding ["a.b.run", "c.a.b.run"] and querying "a.b.run" returns only
    "c.a.b.run" (the one whose FQN ends with ".a.b.run").
    """
    db = _seed_suffix_store(tmp_path, ["a.b.run", "c.a.b.run"])
    with SQLiteStore(str(db)) as store:
        result = store.find_nodes_by_suffix("a.b.run")
    assert [n.id for n in result] == ["c.a.b.run"]


def test_find_nodes_by_suffix_dot_boundary(tmp_path: Path) -> None:
    """Suffix match respects dot boundaries — does not match mid-name."""
    db = _seed_suffix_store(tmp_path, ["a.b.run", "c.run", "x.dry_run"])
    with SQLiteStore(str(db)) as store:
        result = store.find_nodes_by_suffix("run")
    assert [n.id for n in result] == ["a.b.run", "c.run"]  # NOT x.dry_run


def test_find_nodes_by_suffix_escapes_like_wildcards(tmp_path: Path) -> None:
    """Underscores in FQNs are treated as literals, not LIKE wildcards."""
    db = _seed_suffix_store(tmp_path, ["a.tv_distance", "a.tvxdistance"])
    with SQLiteStore(str(db)) as store:
        result = store.find_nodes_by_suffix("tv_distance")
    assert [n.id for n in result] == ["a.tv_distance"]  # _ is literal


def test_find_nodes_by_suffix_dotted_partial(tmp_path: Path) -> None:
    """Multi-segment suffix (e.g. triads.tv_distance) resolves correctly."""
    db = _seed_suffix_store(tmp_path, ["src.cgis.query.drift.triads.tv_distance"])
    with SQLiteStore(str(db)) as store:
        result = store.find_nodes_by_suffix("triads.tv_distance")
    assert [n.id for n in result] == ["src.cgis.query.drift.triads.tv_distance"]


def test_find_nodes_by_suffix_orders_and_limits(tmp_path: Path) -> None:
    """Results are ordered by id and capped at the limit parameter."""
    db = _seed_suffix_store(tmp_path, [f"m{i}.go" for i in range(5)])
    with SQLiteStore(str(db)) as store:
        result = store.find_nodes_by_suffix("go", limit=3)
    assert [n.id for n in result] == ["m0.go", "m1.go", "m2.go"]


def test_find_nodes_by_suffix_no_match_returns_empty(tmp_path: Path) -> None:
    """No matching suffix returns an empty list."""
    db = _seed_suffix_store(tmp_path, ["a.b"])
    with SQLiteStore(str(db)) as store:
        assert store.find_nodes_by_suffix("zzz") == []


def test_find_nodes_by_suffix_closed_store_raises(tmp_path: Path) -> None:
    """Calling find_nodes_by_suffix on a disconnected store raises RuntimeError."""
    store = SQLiteStore(str(tmp_path / "closed.db"))
    with pytest.raises(RuntimeError):
        store.find_nodes_by_suffix("x")


def _named_node(fqn: str, name: str, ntype: NodeType, file_path: str = "f.py") -> Node:
    """Node with an explicit leaf name (search tests need name != id)."""
    return Node(id=fqn, type=ntype, name=name, file_path=file_path, start_line=1, end_line=2)


def test_search_nodes_ranks_exact_prefix_substring(temp_store: SQLiteStore) -> None:
    """search_nodes ranks exact > prefix > substring, tie-break shorter FQN then id (#173)."""
    nodes = [
        _named_node("c.fetch_get_user", "fetch_get_user", NodeType.FUNCTION),  # substring
        _named_node("b.get_user_by_id", "get_user_by_id", NodeType.FUNCTION),  # prefix
        _named_node("scope.get_user", "get_user", NodeType.FUNCTION),  # exact (longer id)
        _named_node("a.get_user", "get_user", NodeType.FUNCTION),  # exact (shorter id)
        _named_node("d.UserService", "UserService", NodeType.CLASS),  # no match
    ]
    temp_store.save_graph(nodes, [])
    result = temp_store.search_nodes("get_user", limit=10)
    assert [n.id for n in result] == [
        "a.get_user",
        "scope.get_user",
        "b.get_user_by_id",
        "c.fetch_get_user",
    ]


def test_search_nodes_kind_and_prefix_filters(temp_store: SQLiteStore) -> None:
    """search_nodes filters by node type and FQN prefix (#173)."""
    nodes = [
        _named_node("app.svc.get_user", "get_user", NodeType.FUNCTION),
        _named_node("app.api.get_user", "get_user", NodeType.FUNCTION),
        _named_node("app.svc.UserModel", "UserModel", NodeType.CLASS),
        # Segment-boundary trap: prefix "app.svc" must NOT match this sibling package.
        _named_node("app.svc_alternative.get_user", "get_user", NodeType.FUNCTION),
    ]
    temp_store.save_graph(nodes, [])
    assert {n.id for n in temp_store.search_nodes("User", kinds=("CLASS",))} == {
        "app.svc.UserModel"
    }
    assert {n.id for n in temp_store.search_nodes("get_user", fqn_prefix="app.svc")} == {
        "app.svc.get_user"
    }


def test_search_nodes_empty_query_returns_empty(temp_store: SQLiteStore) -> None:
    """An empty / whitespace-only query short-circuits to no results (#173)."""
    temp_store.save_graph([_named_node("m.fn", "fn", NodeType.FUNCTION)], [])
    assert temp_store.search_nodes("") == []
    assert temp_store.search_nodes("   ") == []


def test_search_nodes_escapes_wildcards(temp_store: SQLiteStore) -> None:
    """LIKE wildcards in the query are treated literally (#173)."""
    temp_store.save_graph([_named_node("m.a_b", "a_b", NodeType.FUNCTION)], [])
    # '_' must be literal, not a single-char wildcard → 'aXb' must NOT match 'a_b' query
    temp_store.save_graph([_named_node("m.aXb", "aXb", NodeType.FUNCTION)], [])
    assert {n.id for n in temp_store.search_nodes("a_b")} == {"m.a_b"}


def test_clear_wipes_nodes_edges_and_files_state(temp_store: SQLiteStore) -> None:
    """clear() empties the graph and the incremental files_state cache (#192/#223)."""
    nodes, edges = _seed_data()
    temp_store.save_graph(nodes, edges)
    temp_store.upsert_file_hash("f.py", "deadbeef")
    assert temp_store.get_node_count() == len(nodes)
    assert temp_store.get_edge_count() == len(edges)
    assert temp_store.get_all_tracked_files() == {"f.py"}

    temp_store.clear()

    assert temp_store.get_node_count() == 0
    assert temp_store.get_edge_count() == 0
    assert temp_store.get_all_nodes() == []
    assert temp_store.get_all_tracked_files() == set()


def test_count_helpers_avoid_deserializing_the_graph(temp_store: SQLiteStore) -> None:
    """get_node_count/get_edge_count return the same totals as the heavier paths."""
    nodes, edges = _seed_data()
    temp_store.save_graph(nodes, edges)
    assert temp_store.get_node_count() == len(temp_store.get_all_nodes())
    assert temp_store.get_edge_count() == temp_store.get_edge_stats().total
