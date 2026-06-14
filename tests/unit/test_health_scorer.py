"""Unit tests for HealthScorer."""

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.analysis.health import HealthScorer


def _node(fqn: str, ntype: NodeType = NodeType.FUNCTION, fp: str = "a.py") -> Node:
    """Create a minimal Node for testing."""
    return Node(
        id=fqn,
        type=ntype,
        name=fqn,
        file_path=fp,
        start_line=1,
        end_line=10,
        namespace=NodeNamespace.INTERNAL,
    )


def _edge(src: str, tgt: str, etype: EdgeType = EdgeType.CALLS) -> Edge:
    """Create a minimal Edge for testing."""
    return Edge(id=f"{src}->{tgt}", source=src, target=tgt, type=etype)


def test_fan_out_counts_behavioral_outgoing() -> None:
    """fan_out increments for each behavioral outgoing edge."""
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [_edge("a", "b"), _edge("a", "c")]
    result = HealthScorer(nodes, edges).enrich()
    a = next(n for n in result if n.id == "a")
    assert a.metadata["fan_out"] == 2


def test_fan_in_counts_behavioral_incoming() -> None:
    """fan_in increments for each behavioral incoming edge."""
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [_edge("b", "a"), _edge("c", "a")]
    result = HealthScorer(nodes, edges).enrich()
    a = next(n for n in result if n.id == "a")
    assert a.metadata["fan_in"] == 2


def test_contains_edges_excluded_from_fan() -> None:
    """CONTAINS edges do not count toward fan_in or fan_out."""
    nodes = [_node("file", NodeType.FILE), _node("fn")]
    edges = [_edge("file", "fn", EdgeType.CONTAINS)]
    result = HealthScorer(nodes, edges).enrich()
    file_node = next(n for n in result if n.id == "file")
    assert file_node.metadata["fan_out"] == 0
    assert file_node.metadata["fan_in"] == 0


def test_depth_file_is_zero() -> None:
    """FILE nodes are at depth 0 (roots of the structural tree)."""
    file_node = _node("mod", NodeType.FILE, fp="mod.py")
    result = HealthScorer([file_node], []).enrich()
    assert result[0].metadata["depth"] == 0


def test_depth_function_inside_file_is_one() -> None:
    """A function directly inside a FILE has depth 1."""
    file_node = _node("mod", NodeType.FILE, fp="mod.py")
    fn = _node("mod.fn", NodeType.FUNCTION, fp="mod.py")
    edges = [_edge("mod", "mod.fn", EdgeType.CONTAINS)]
    result = HealthScorer([file_node, fn], edges).enrich()
    fn_result = next(n for n in result if n.id == "mod.fn")
    assert fn_result.metadata["depth"] == 1


def test_depth_method_inside_class_is_two() -> None:
    """A method nested inside class inside file has depth 2."""
    file_node = _node("mod", NodeType.FILE)
    cls = _node("mod.Cls", NodeType.CLASS)
    method = _node("mod.Cls.m", NodeType.METHOD)
    edges = [
        _edge("mod", "mod.Cls", EdgeType.CONTAINS),
        _edge("mod.Cls", "mod.Cls.m", EdgeType.CONTAINS),
    ]
    result = HealthScorer([file_node, cls, method], edges).enrich()
    m = next(n for n in result if n.id == "mod.Cls.m")
    assert m.metadata["depth"] == 2


def test_in_cycle_true_for_cyclic_imports() -> None:
    """Both nodes in a mutual IMPORTS cycle are marked in_cycle=True."""
    a = _node("a", NodeType.FILE, fp="a.py")
    b = _node("b", NodeType.FILE, fp="b.py")
    edges = [
        _edge("a", "b", EdgeType.IMPORTS),
        _edge("b", "a", EdgeType.IMPORTS),
    ]
    result = HealthScorer([a, b], edges).enrich()
    a_result = next(n for n in result if n.id == "a")
    b_result = next(n for n in result if n.id == "b")
    assert a_result.metadata["in_cycle"] is True
    assert b_result.metadata["in_cycle"] is True


def test_in_cycle_false_for_dag() -> None:
    """A node in a DAG (no cycles) is marked in_cycle=False."""
    a = _node("a", NodeType.FILE, fp="a.py")
    b = _node("b", NodeType.FILE, fp="b.py")
    edges = [_edge("a", "b", EdgeType.IMPORTS)]
    result = HealthScorer([a, b], edges).enrich()
    a_result = next(n for n in result if n.id == "a")
    assert a_result.metadata["in_cycle"] is False


def test_in_cycle_propagates_to_methods_in_cyclic_file() -> None:
    """Methods inside a cyclic file must also report in_cycle=True."""
    file_a = _node("a", NodeType.FILE, fp="a.py")
    file_b = _node("b", NodeType.FILE, fp="b.py")
    cls = _node("a.Cls", NodeType.CLASS, fp="a.py")
    method = _node("a.Cls.m", NodeType.METHOD, fp="a.py")
    edges = [
        _edge("a", "b", EdgeType.IMPORTS),
        _edge("b", "a", EdgeType.IMPORTS),
        _edge("a", "a.Cls", EdgeType.CONTAINS),
        _edge("a.Cls", "a.Cls.m", EdgeType.CONTAINS),
    ]
    result = HealthScorer([file_a, file_b, cls, method], edges).enrich()
    method_result = next(n for n in result if n.id == "a.Cls.m")
    assert method_result.metadata["in_cycle"] is True


def test_enrich_preserves_existing_metadata() -> None:
    """Pre-existing metadata keys survive the enrich() call."""
    n = Node(
        id="x",
        type=NodeType.FUNCTION,
        name="x",
        file_path="x.py",
        start_line=1,
        end_line=5,
        namespace=NodeNamespace.INTERNAL,
        metadata={"custom_key": "keep_me"},
    )
    result = HealthScorer([n], []).enrich()
    assert result[0].metadata["custom_key"] == "keep_me"
    assert "fan_out" in result[0].metadata


def test_external_nodes_get_zero_metrics() -> None:
    """External nodes (file_path=EXTERNAL) receive zero for all numeric metrics."""
    ext = Node(
        id="ext",
        type=NodeType.FUNCTION,
        name="ext",
        file_path="EXTERNAL",
        start_line=1,
        end_line=1,
        namespace=NodeNamespace.EXTERNAL,
    )
    result = HealthScorer([ext], []).enrich()
    assert result[0].metadata["fan_out"] == 0
    assert result[0].metadata["in_cycle"] is False
