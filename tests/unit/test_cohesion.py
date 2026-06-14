"""Unit tests for cgis.query.analysis.cohesion — intra-package file graph builder (#242)."""

import pytest
from conftest import make_file_node, make_import_edge

from cgis.core.models import Edge, EdgeType
from cgis.query.analysis.cohesion import (
    THRESHOLDS,
    FileGraph,
    build_file_graph,
    classify_verdict,
    greedy_modularity,
    layout_direction,
    partition_divergence,
)


def test_build_file_graph_aggregates_internal_imports() -> None:
    nodes = [
        make_file_node("p.a", "p/a.py"),
        make_file_node("p.b", "p/b.py"),
        make_file_node("p.c", "p/c.py"),
    ]
    edges = [
        make_import_edge("p.a", "p.b"),
        make_import_edge("p.a", "p.b"),
        make_import_edge("p.b", "p.c"),
    ]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert set(g.files) == {"p.a", "p.b", "p.c"}
    assert g.adj["p.a"]["p.b"] == pytest.approx(2.0)
    assert g.adj["p.b"]["p.a"] == pytest.approx(2.0)
    assert g.adj["p.b"]["p.c"] == pytest.approx(1.0)


def test_build_file_graph_reconciles_cross_rooted_targets() -> None:
    nodes = [make_file_node("p.a", "p/a.py"), make_file_node("p.b", "p/b.py")]
    edges = [make_import_edge("p.a", "cgis.p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"]["p.b"] == pytest.approx(1.0)


def test_build_file_graph_keeps_isolated_files() -> None:
    nodes = [
        make_file_node("p.a", "p/a.py"),
        make_file_node("p.b", "p/b.py"),
        make_file_node("p.leaf", "p/leaf.py"),
    ]
    edges = [make_import_edge("p.a", "p.b")]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert "p.leaf" in g.files
    assert g.adj.get("p.leaf", {}) == {}


def test_build_file_graph_excludes_external_and_self_loops() -> None:
    nodes = [make_file_node("p.a", "p/a.py"), make_file_node("p.b", "p/b.py")]
    edges = [
        make_import_edge("p.a", "enum"),
        make_import_edge("p.a", "p.a"),
        make_import_edge("p.a", "p.b"),
    ]
    g = build_file_graph(nodes, edges, prefix="p", with_calls=False)
    assert g.adj["p.a"] == {"p.b": 1.0}


def test_build_file_graph_with_calls_adds_calls_layer() -> None:
    nodes = [make_file_node("p.a", "p/a.py"), make_file_node("p.b", "p/b.py")]
    call = Edge(
        id="p.a:CALLS:p.b",
        source="p.a",
        target="p.b",
        type=EdgeType.CALLS,
        weight=1.0,
        confidence=1.0,
    )
    g = build_file_graph(nodes, [call], prefix="p", with_calls=True)
    assert g.adj["p.a"]["p.b"] == pytest.approx(1.0)
    g_imports_only = build_file_graph(nodes, [call], prefix="p", with_calls=False)
    assert g_imports_only.adj == {}


def _clique(prefix: str, names: list[str]) -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {}
    ids = [f"{prefix}.{n}" for n in names]
    for a in ids:
        for b in ids:
            if a != b:
                adj.setdefault(a, {})[b] = 1.0
    return adj


def test_modularity_two_disconnected_cliques() -> None:
    adj = {**_clique("p", ["a", "b", "c"]), **_clique("p", ["x", "y", "z"])}
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    communities, q = greedy_modularity(g)
    assert len(communities) == 2
    assert q == pytest.approx(0.5, abs=0.05)
    assert {frozenset(c) for c in communities} == {
        frozenset({"p.a", "p.b", "p.c"}),
        frozenset({"p.x", "p.y", "p.z"}),
    }


def test_modularity_single_clique_is_one_community() -> None:
    adj = _clique("p", ["a", "b", "c"])
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    communities, q = greedy_modularity(g)
    assert len(communities) == 1
    assert q == pytest.approx(0.0, abs=0.05)


def test_modularity_isolated_files_are_singletons() -> None:
    adj = _clique("p", ["a", "b", "c"])
    g = FileGraph(files=(*sorted(adj), "p.leaf"), adj=adj)
    communities, q = greedy_modularity(g)
    assert ["p.leaf"] in communities
    assert q == pytest.approx(0.0, abs=0.05)


def test_modularity_is_deterministic() -> None:
    adj = {**_clique("p", ["a", "b", "c"]), **_clique("p", ["x", "y", "z"])}
    g = FileGraph(files=tuple(sorted(adj)), adj=adj)
    assert greedy_modularity(g) == greedy_modularity(g)


def test_divergence_flat_layout_vs_multi_community_is_one() -> None:
    p_comm = {"a": 0, "b": 0, "c": 1, "d": 1}
    p_dir = {"a": "<root>", "b": "<root>", "c": "<root>", "d": "<root>"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(1.0)


def test_divergence_aligned_layout_is_zero() -> None:
    p_comm = {"a": 0, "b": 0, "c": 1, "d": 1}
    p_dir = {"a": "x", "b": "x", "c": "y", "d": "y"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(0.0)


def test_divergence_is_symmetric() -> None:
    p_comm = {"a": 0, "b": 1, "c": 1}
    p_dir = {"a": "x", "b": "y", "c": "y"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(partition_divergence(p_dir, p_comm))


def test_divergence_both_trivial_is_zero() -> None:
    p_comm = {"a": 0, "b": 0}
    p_dir = {"a": "<root>", "b": "<root>"}
    assert partition_divergence(p_comm, p_dir) == pytest.approx(0.0)


def test_layout_direction() -> None:
    assert layout_direction({"a": 0, "b": 1}, {"a": "<root>", "b": "<root>"}) == "under_split"
    assert (
        layout_direction({"a": 0, "b": 0, "c": 0}, {"a": "x", "b": "y", "c": "z"}) == "over_split"
    )
    assert layout_direction({"a": 0, "b": 1}, {"a": "x", "b": "y"}) == "matched"


def test_verdict_table() -> None:
    t = THRESHOLDS
    assert classify_verdict(q=0.10, d=1.0, direction="under_split", thresholds=t) == "leave"
    assert classify_verdict(q=0.50, d=0.05, direction="matched", thresholds=t) == "aligned"
    assert classify_verdict(q=0.43, d=1.0, direction="under_split", thresholds=t) == "split"
    assert classify_verdict(q=0.43, d=0.8, direction="over_split", thresholds=t) == "consolidate"
    assert classify_verdict(q=0.43, d=0.8, direction="matched", thresholds=t) == "borderline"
    assert classify_verdict(q=0.30, d=1.0, direction="under_split", thresholds=t) == "borderline"


def test_verdict_min_q_override() -> None:
    t = THRESHOLDS
    strict = {**t, "split": 0.50}
    result = classify_verdict(q=0.43, d=1.0, direction="under_split", thresholds=strict)
    assert result == "borderline"
