"""Unit tests for the JSON graph serializer (#171)."""

from cgis.core.coverage import TraversalCoverage
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.render.graph_json import graph_to_json


def _make_node(node_id: str, node_type: NodeType = NodeType.FUNCTION) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.rsplit(".", maxsplit=1)[-1],
        file_path="src/mod.py",
        start_line=7,
        end_line=9,
        language="python",
    )


def _make_edge(
    source: str, target: str, edge_type: EdgeType = EdgeType.CALLS, confidence: float = 1.0
) -> Edge:
    return Edge(
        id=f"{source}->{target}",
        source=source,
        target=target,
        type=edge_type,
        confidence=confidence,
    )


def test_graph_to_json_emits_real_fqns_not_hashes() -> None:
    """Nodes carry real FQNs + type/file/line; no display hashes."""
    nodes = [_make_node("pkg.mod.handler")]
    edges: list[Edge] = []

    payload = graph_to_json("pkg.mod.handler", nodes, edges)

    assert payload["root"] == "pkg.mod.handler"
    assert payload["nodes"] == [
        {"fqn": "pkg.mod.handler", "type": "FUNCTION", "file": "src/mod.py", "line": 7}
    ]


def test_graph_to_json_edges_carry_endpoints_type_and_confidence() -> None:
    """Edges are joinable: src/dst are real FQNs, with type and confidence."""
    nodes = [_make_node("pkg.a"), _make_node("pkg.b")]
    edges = [_make_edge("pkg.a", "pkg.b", confidence=0.5)]

    payload = graph_to_json("pkg.a", nodes, edges)

    assert payload["edges"] == [
        {"src": "pkg.a", "dst": "pkg.b", "type": "CALLS", "confidence": 0.5}
    ]


def test_graph_to_json_preserves_unresolved_raw_call_targets() -> None:
    """Unresolved targets keep their raw_call: prefix so agents can detect them."""
    nodes = [_make_node("pkg.a")]
    edges = [_make_edge("pkg.a", "raw_call:mystery", confidence=0.1)]

    payload = graph_to_json("pkg.a", nodes, edges)

    assert payload["edges"][0]["dst"] == "raw_call:mystery"


def test_graph_to_json_carries_coverage_when_given() -> None:
    """A traversal's local coverage rides in the same payload as its subgraph (#201)."""
    coverage = TraversalCoverage(
        basis="unresolved_calls_made",
        calls_examined=4,
        calls_unresolved=1,
        top_unresolved=[("logger.info", 1)],
    )

    payload = graph_to_json("pkg.a", [_make_node("pkg.a")], [], coverage=coverage)

    assert payload["coverage"] == {
        "basis": "unresolved_calls_made",
        "calls_examined": 4,
        "calls_unresolved": 1,
        "top_unresolved": [("logger.info", 1)],
        "unresolved_ratio": 0.25,
    }


def test_graph_to_json_omits_coverage_when_none() -> None:
    """Structure queries follow no calls, so they carry no coverage key at all."""
    payload = graph_to_json("pkg.a", [_make_node("pkg.a")], [])

    assert "coverage" not in payload
