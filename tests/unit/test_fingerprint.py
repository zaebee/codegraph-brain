"""Unit tests for PatternFingerprint and FingerprintExtractor."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.fingerprint import FingerprintExtractor, PatternFingerprint
from cgis.query.triads import TRIAD_ORDER
from cgis.storage.sqlite_store import SQLiteStore

# ── helpers ──────────────────────────────────────────────────────────────────


def _node(fqn: str, ntype: NodeType = NodeType.FUNCTION, fp: str = "a.py") -> Node:
    return Node(
        id=fqn,
        type=ntype,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=fp,
        start_line=1,
        end_line=10,
        namespace=NodeNamespace.INTERNAL,
    )


def _edge(src: str, tgt: str, etype: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(id=f"{src}:{etype.value}:{tgt}", source=src, target=tgt, type=etype)


def _store(nodes: list[Node], edges: list[Edge]) -> SQLiteStore:
    s = SQLiteStore(":memory:")
    s.connect()
    s.save_graph(nodes, edges, overwrite=True)
    return s


# ── _in_domain boundary tests ────────────────────────────────────────────────


def test_prefix_boundary_no_false_positive() -> None:
    """'dom_extra' must not be included when extracting domain 'dom'."""
    n1 = _node("dom.fn")
    n2 = _node("dom_extra.fn")
    with _store([n1, n2], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 0
    assert fp.unresolved_ratio == pytest.approx(0.0)


def test_exact_prefix_match_is_included() -> None:
    """A node whose id equals the fqn_prefix exactly must be included."""
    n = _node("dom", NodeType.FILE, fp="dom.py")
    with _store([n], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == pytest.approx(0.0)  # node exists, no cycles


# ── empty domain ──────────────────────────────────────────────────────────────


def test_empty_domain_returns_zero_fingerprint() -> None:
    """No matching nodes → all-zero PatternFingerprint."""
    with _store([], []) as store:
        fp = FingerprintExtractor(store).extract("nonexistent")
    assert fp == PatternFingerprint(
        domain="nonexistent",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )


# ── hub_count ─────────────────────────────────────────────────────────────────


def test_hub_count_detects_high_fan_in_zero_fan_out() -> None:
    """Node with fan_in > 2 and fan_out == 0 is a hub."""
    nodes = [
        _node("dom.hub"),
        _node("dom.c1"),
        _node("dom.c2"),
        _node("dom.c3"),
    ]
    edges = [
        _edge("dom.c1", "dom.hub"),
        _edge("dom.c2", "dom.hub"),
        _edge("dom.c3", "dom.hub"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 1


def test_hub_count_zero_when_hub_has_outgoing() -> None:
    """A node with fan_in > 2 but fan_out > 0 is NOT a hub."""
    nodes = [_node("dom.almost"), _node("dom.c1"), _node("dom.c2"), _node("dom.c3"), _node("dom.x")]
    edges = [
        _edge("dom.c1", "dom.almost"),
        _edge("dom.c2", "dom.almost"),
        _edge("dom.c3", "dom.almost"),
        _edge("dom.almost", "dom.x"),  # has outgoing — disqualified
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 0


# ── star_count ────────────────────────────────────────────────────────────────


def test_star_count_detects_high_fan_out_low_fan_in() -> None:
    """Node with fan_out > 3 and fan_in <= 1 is a star."""
    nodes = [_node("dom.star"), _node("dom.l1"), _node("dom.l2"), _node("dom.l3"), _node("dom.l4")]
    edges = [
        _edge("dom.star", "dom.l1"),
        _edge("dom.star", "dom.l2"),
        _edge("dom.star", "dom.l3"),
        _edge("dom.star", "dom.l4"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.star_count == 1


def test_star_count_ignores_external_calls() -> None:
    """Calls to targets outside the domain (stdlib, other domains) must not create a star."""
    nodes = [_node("dom.fn")]
    edges = [
        _edge("dom.fn", "pathlib.Path"),
        _edge("dom.fn", "json.dumps"),
        _edge("dom.fn", "other.helper"),
        _edge("dom.fn", "os.getcwd"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.star_count == 0


def test_hub_count_ignores_external_callers() -> None:
    """Incoming calls from outside the domain must not create a hub."""
    nodes = [
        _node("dom.util"),
        _node("other.c1"),
        _node("other.c2"),
        _node("other.c3"),
    ]
    edges = [
        _edge("other.c1", "dom.util"),
        _edge("other.c2", "dom.util"),
        _edge("other.c3", "dom.util"),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.hub_count == 0


def test_star_count_zero_when_fan_out_is_three() -> None:
    """fan_out == 3 does not qualify as a star (threshold is > 3)."""
    nodes = [_node("dom.n"), _node("dom.l1"), _node("dom.l2"), _node("dom.l3")]
    edges = [_edge("dom.n", "dom.l1"), _edge("dom.n", "dom.l2"), _edge("dom.n", "dom.l3")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.star_count == 0


# ── cycle_ratio ───────────────────────────────────────────────────────────────


def test_cycle_ratio_is_one_for_fully_cyclic_domain() -> None:
    """Two FILE nodes that import each other → cycle_ratio = 1.0."""
    f1 = _node("dom.modA", NodeType.FILE, fp="dom/modA.py")
    f2 = _node("dom.modB", NodeType.FILE, fp="dom/modB.py")
    edges = [
        _edge("dom.modA", "dom.modB", EdgeType.IMPORTS),
        _edge("dom.modB", "dom.modA", EdgeType.IMPORTS),
    ]
    with _store([f1, f2], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == pytest.approx(1.0)


def test_cycle_ratio_zero_when_no_cycles() -> None:
    """DAG imports → cycle_ratio = 0.0."""
    f1 = _node("dom.a", NodeType.FILE, fp="dom/a.py")
    f2 = _node("dom.b", NodeType.FILE, fp="dom/b.py")
    edges = [_edge("dom.a", "dom.b", EdgeType.IMPORTS)]
    with _store([f1, f2], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.cycle_ratio == pytest.approx(0.0)


# ── unresolved_ratio ──────────────────────────────────────────────────────────


def test_unresolved_ratio_half_when_one_raw_call() -> None:
    """1 raw_call + 1 resolved out of 2 CALLS → unresolved_ratio = 0.5."""
    caller = _node("dom.fn")
    target = _node("dom.other")
    edges = [
        _edge("dom.fn", "raw_call:something", EdgeType.CALLS),
        _edge("dom.fn", "dom.other", EdgeType.CALLS),
    ]
    with _store([caller, target], edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.unresolved_ratio == pytest.approx(0.5)


def test_unresolved_ratio_zero_when_no_calls() -> None:
    """Domain with no CALLS edges → unresolved_ratio = 0.0 (not a division-by-zero)."""
    n = _node("dom.fn")
    with _store([n], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.unresolved_ratio == pytest.approx(0.0)


# ── chain_len ──────────────────────────────────────────────────────────────────


def test_chain_len_linear_three_node_chain() -> None:
    """A → B → C within domain: chain_len = 2.0."""
    nodes = [_node("dom.a"), _node("dom.b"), _node("dom.c")]
    edges = [_edge("dom.a", "dom.b"), _edge("dom.b", "dom.c")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.chain_len == pytest.approx(2.0)


def test_chain_len_zero_for_single_isolated_node() -> None:
    """A domain with a single node and no CALLS edges → chain_len = 0.0."""
    with _store([_node("dom.x")], []) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.chain_len == pytest.approx(0.0)


# ── dag_depth ─────────────────────────────────────────────────────────────────


def test_dag_depth_three_level_import_chain() -> None:
    """A imports B imports C (within domain) → dag_depth = 2."""
    nodes = [
        _node("dom.a", NodeType.FILE, fp="dom/a.py"),
        _node("dom.b", NodeType.FILE, fp="dom/b.py"),
        _node("dom.c", NodeType.FILE, fp="dom/c.py"),
    ]
    edges = [
        _edge("dom.a", "dom.b", EdgeType.IMPORTS),
        _edge("dom.b", "dom.c", EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("dom")
    assert fp.dag_depth == 2


# ── fingerprint v2: triad census vectors (spec §3.2) ─────────────────────────


def test_fingerprint_defaults_zero_census() -> None:
    """t_imports/t_calls default to the 13-dim zero vector (back-compat)."""
    fp = PatternFingerprint(
        domain="d",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    assert fp.t_imports == (0.0,) * 13
    assert fp.t_calls == (0.0,) * 13


def test_extract_populates_calls_census() -> None:
    """A domain with an internal A→B→C CALLS path measures t_calls ∝ e_021C."""
    nodes = [_node("m.a"), _node("m.b"), _node("m.c")]
    edges = [_edge("m.a", "m.b"), _edge("m.b", "m.c")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("m")
    idx = TRIAD_ORDER.index("021C")
    assert fp.t_calls[idx] == pytest.approx(1.0)
    assert sum(fp.t_calls) == pytest.approx(1.0)
    assert fp.t_imports == (0.0,) * 13  # no IMPORTS edges


def test_from_graph_skips_store() -> None:
    """from_graph() builds an extractor over in-memory nodes/edges (quotient path)."""
    nodes = [_node("q.a"), _node("q.b"), _node("q.c")]
    edges = [_edge("q.a", "q.b"), _edge("q.a", "q.c")]
    fp = FingerprintExtractor.from_graph(nodes, edges).extract("q")
    assert fp.t_calls[TRIAD_ORDER.index("021D")] == pytest.approx(1.0)
