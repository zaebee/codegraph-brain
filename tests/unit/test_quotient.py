"""Tests for the domain-quotient graph builder (spec §3.4)."""

import pytest

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.query.drift.drift import DomainConfig
from cgis.query.drift.fingerprint import FingerprintExtractor
from cgis.query.drift.quotient import QUOTIENT_PREFIX, build_quotient, quotient_call_tally
from cgis.query.drift.triads import TRIAD_ORDER


def _node(fqn: str) -> Node:
    return Node(
        id=fqn,
        type=NodeType.FUNCTION,
        name=fqn.rsplit(".", 1)[-1],
        file_path="x.py",
        start_line=1,
        end_line=2,
    )


def _edge(source: str, target: str, etype: EdgeType) -> Edge:
    return Edge(
        id=f"{source}:{etype.value}:{target}",
        source=source,
        target=target,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def _domain(name: str, prefix: str) -> DomainConfig:
    return DomainConfig(name=name, fqn_prefix=prefix, expected_pattern=None, drift_tolerance=0.5)


_DOMAINS = [_domain("ext", "p.ext"), _domain("res", "p.res"), _domain("sto", "p.sto")]

_NODES = [_node("p.ext.a"), _node("p.res.b"), _node("p.sto.c"), _node("other.z")]

_EDGES = [
    _edge("p.ext.a", "p.res.b", EdgeType.CALLS),  # cross-domain → kept
    _edge("p.ext.a", "p.res.b", EdgeType.IMPORTS),  # same pair, other layer → kept
    _edge("p.res.b", "p.sto.c", EdgeType.CALLS),  # cross-domain → kept
    _edge("p.ext.a", "p.ext.a", EdgeType.CALLS),  # intra-domain → dropped
    _edge("p.ext.a", "other.z", EdgeType.CALLS),  # target in no domain → dropped
    _edge("p.ext.a", "p.res.b", EdgeType.CALLS),  # duplicate pair → aggregated
]


def test_quotient_nodes_one_per_domain() -> None:
    """One MODULE node per domain, id = quotient.<name>."""
    qnodes, _ = build_quotient(_NODES, _EDGES, _DOMAINS)
    assert sorted(n.id for n in qnodes) == [
        f"{QUOTIENT_PREFIX}.ext",
        f"{QUOTIENT_PREFIX}.res",
        f"{QUOTIENT_PREFIX}.sto",
    ]
    assert all(n.type == NodeType.MODULE for n in qnodes)


def test_quotient_edges_aggregated_cross_domain_only() -> None:
    """Cross-domain edges aggregate per (src, dst, type) with weight = count."""
    _, qedges = build_quotient(_NODES, _EDGES, _DOMAINS)
    by_key = {(e.source, e.target, e.type): e for e in qedges}
    calls = by_key[(f"{QUOTIENT_PREFIX}.ext", f"{QUOTIENT_PREFIX}.res", EdgeType.CALLS)]
    assert calls.weight == pytest.approx(2.0)  # the duplicate aggregated
    assert (f"{QUOTIENT_PREFIX}.ext", f"{QUOTIENT_PREFIX}.res", EdgeType.IMPORTS) in by_key
    assert len(qedges) == 3  # intra-domain and out-of-domain edges never appear


def test_quotient_chain_measures_021c() -> None:
    """ext→res→sto CALLS chain scores t_calls ∝ e_021C through the SAME extractor."""
    qnodes, qedges = build_quotient(_NODES, _EDGES, _DOMAINS)
    fp = FingerprintExtractor.from_graph(qnodes, qedges).extract(QUOTIENT_PREFIX)
    assert fp.t_calls[TRIAD_ORDER.index("021C")] == pytest.approx(1.0)


def test_quotient_carries_the_members_unresolved_share() -> None:
    """The quotient's unresolved_ratio is the call-weighted mean of its members' (#149).

    build_quotient keeps no unresolved edge, so without the tally the k=1 CALLS
    layer read 0 unresolved and went undiscounted however poorly the code under
    it resolved.
    """
    unknown = _node("console.print").model_copy(update={"namespace": NodeNamespace.UNKNOWN})
    nodes = [*_NODES, unknown]
    edges = [
        *_EDGES,  # ext: 4 resolved CALLS; res: 1
        _edge("p.ext.a", "console.print", EdgeType.CALLS),  # ext: 1 unresolved of 5
        _edge("p.res.b", "raw_call:mystery", EdgeType.CALLS),  # res: 1 unresolved of 2
    ]
    qnodes, qedges = build_quotient(nodes, edges, _DOMAINS)
    tally = quotient_call_tally(nodes, edges, _DOMAINS)
    assert tally == {f"{QUOTIENT_PREFIX}.ext": (1, 5), f"{QUOTIENT_PREFIX}.res": (1, 2)}

    fp = FingerprintExtractor.from_graph(qnodes, qedges, tally).extract(QUOTIENT_PREFIX)
    assert fp.unresolved_ratio == pytest.approx(2 / 7)
    bare = FingerprintExtractor.from_graph(qnodes, qedges).extract(QUOTIENT_PREFIX)
    assert bare.unresolved_ratio == pytest.approx(0.0)
