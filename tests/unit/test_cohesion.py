"""Unit tests for cgis.query.cohesion — intra-package file graph builder (#242)."""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.cohesion import build_file_graph


def _file(fqn: str, path: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", 1)[-1],
        file_path=path,
        start_line=0,
        end_line=0,
    )


def _imp(src: str, tgt: str) -> Edge:
    return Edge(
        id=f"{src}:IMPORTS:{tgt}",
        source=src,
        target=tgt,
        type=EdgeType.IMPORTS,
        weight=1.0,
        confidence=1.0,
    )


def test_build_file_graph_aggregates_internal_imports() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py"), _file("p.c", "p/c.py")]
    edges = [_imp("p.a", "p.b"), _imp("p.a", "p.b"), _imp("p.b", "p.c")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert set(g.files) == {"p.a", "p.b", "p.c"}
    assert g.adj["p.a"]["p.b"] == 2.0
    assert g.adj["p.b"]["p.a"] == 2.0
    assert g.adj["p.b"]["p.c"] == 1.0


def test_build_file_graph_reconciles_cross_rooted_targets() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    edges = [_imp("p.a", "cgis.p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"]["p.b"] == 1.0


def test_build_file_graph_keeps_isolated_files() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py"), _file("p.leaf", "p/leaf.py")]
    edges = [_imp("p.a", "p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert "p.leaf" in g.files
    assert g.adj.get("p.leaf", {}) == {}


def test_build_file_graph_excludes_external_and_self_loops() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    edges = [_imp("p.a", "enum"), _imp("p.a", "p.a"), _imp("p.a", "p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"] == {"p.b": 1.0}


def test_build_file_graph_with_calls_adds_calls_layer() -> None:
    nodes = [_file("p.a", "p/a.py"), _file("p.b", "p/b.py")]
    call = Edge(
        id="p.a:CALLS:p.b",
        source="p.a",
        target="p.b",
        type=EdgeType.CALLS,
        weight=1.0,
        confidence=1.0,
    )
    g = build_file_graph(nodes, [call], prefix="p", with_calls=True)
    assert g.adj["p.a"]["p.b"] == 1.0
    g_imports_only = build_file_graph(nodes, [call], prefix="p", with_calls=False)
    assert g_imports_only.adj == {}
