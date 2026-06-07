"""Unit test cases for sqlite storage."""

from collections.abc import Generator

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.engine import QueryEngine
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
        Node(id="mod.func_a", type=NodeType.FUNCTION, name="func_a",
             file_path="mod.py", start_line=1, end_line=2),
        Node(id="mod.func_b", type=NodeType.FUNCTION, name="func_b",
             file_path="mod.py", start_line=3, end_line=4),
    ]
    edges = [Edge(id="e1", source="mod.func_a", target="mod.func_b",
                  type=EdgeType.CALLS, file_path="mod.py")]
    temp_store.save_graph(nodes, edges)
    temp_store.upsert_file_hash("mod.py", "abc")

    temp_store.delete_file_data("mod.py")

    assert temp_store.get_node("mod.func_a") is None
    assert temp_store.get_node("mod.func_b") is None
    assert temp_store.get_outgoing_edges("mod.func_a") == []
    assert temp_store.get_file_hash("mod.py") is None


def test_get_nodes_by_file(temp_store: SQLiteStore) -> None:
    """get_nodes_by_file returns only nodes for the requested file."""
    nodes = [
        Node(id="a.func", type=NodeType.FUNCTION, name="func",
             file_path="a.py", start_line=1, end_line=2),
        Node(id="b.func", type=NodeType.FUNCTION, name="func",
             file_path="b.py", start_line=1, end_line=2),
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
