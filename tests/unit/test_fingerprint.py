"""Unit tests for PatternFingerprint and FingerprintExtractor."""

from pathlib import Path

import pytest
from conftest import module_with_funcs

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.pipeline import IngestionPipeline
from cgis.query.drift.fingerprint import (
    FingerprintExtractor,
    PatternFingerprint,
    _follow_forwarding,
    _reattributed_imports,
)
from cgis.query.drift.triads import TRIAD_ORDER
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
    """No matching nodes → all-zero PatternFingerprint with node_count/edge_count == 0."""
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
        node_count=0,
        edge_count=0,
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


def test_extractor_without_store_or_preload_raises() -> None:
    """Constructing with store=None and no from_graph() preload fails loud."""
    extractor = FingerprintExtractor(None)

    with pytest.raises(RuntimeError, match="store or a from_graph"):
        extractor.extract("m")


# ── node_count / edge_count (#178 task 1) ────────────────────────────────────


def test_extract_sets_node_and_edge_counts() -> None:
    """extract() over a real domain records actual node/edge counts in the fingerprint."""
    nodes = [_node("real.a"), _node("real.b"), _node("real.c")]
    edges = [_edge("real.a", "real.b"), _edge("real.b", "real.c")]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("real")
    assert fp.node_count >= 2
    assert fp.edge_count >= 1


def test_extract_zero_match_sets_zero_counts() -> None:
    """extract() with an unmatched prefix produces node_count == 0 and edge_count == 0."""
    with _store([], []) as store:
        fp = FingerprintExtractor(store).extract("totally.missing.prefix")
    assert fp.node_count == 0
    assert fp.edge_count == 0


def test_extract_isolated_node_has_no_signal_counts() -> None:
    """A domain with one node and no intra-domain edges gives node_count=1, edge_count=0."""
    with _store([_node("lone.x")], []) as store:
        fp = FingerprintExtractor(store).extract("lone")
    assert fp.node_count == 1
    assert fp.edge_count == 0


def test_hand_built_fingerprint_defaults_are_measurable() -> None:
    """PatternFingerprint constructed without new kwargs has node_count==1, edge_count==1.

    Amendment 1: hand-built fingerprints are assumed measurable by default.
    """
    fp = PatternFingerprint(
        domain="hand",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=0.0,
    )
    assert fp.node_count == 1
    assert fp.edge_count == 1


# ---------------------------------------------------------------------------
# intra-domain cycle_ratio (#176)
# ---------------------------------------------------------------------------


def test_single_file_domain_in_cross_cycle_has_zero_cycle_ratio() -> None:
    """The httpx case: a one-file domain inside a CROSS-domain import cycle → 0.0."""
    nodes = module_with_funcs("pkg.alpha", "pkg/alpha.py", 3) + module_with_funcs(
        "pkg.beta", "pkg/beta.py", 3
    )
    edges = [
        Edge(id="i1", source="pkg.alpha", target="pkg.beta", type=EdgeType.IMPORTS),
        Edge(id="i2", source="pkg.beta", target="pkg.alpha", type=EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("pkg.alpha")
    assert fp.cycle_ratio == pytest.approx(
        0.0
    )  # the cycle is cross-domain; not this domain's smell


def test_intra_domain_cycle_counts_blast_radius() -> None:
    """Two modules of ONE domain importing each other → their nodes count."""
    nodes = (
        module_with_funcs("app.svc.a", "app/svc/a.py", 2)
        + module_with_funcs("app.svc.b", "app/svc/b.py", 2)
        + module_with_funcs("app.svc.clean", "app/svc/clean.py", 2)
    )
    edges = [
        Edge(id="i1", source="app.svc.a", target="app.svc.b", type=EdgeType.IMPORTS),
        Edge(id="i2", source="app.svc.b", target="app.svc.a", type=EdgeType.IMPORTS),
        Edge(id="i3", source="app.svc.clean", target="app.svc.a", type=EdgeType.IMPORTS),
    ]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("app.svc")
    # 6 of 9 nodes live in the two cyclic files (module + 2 funcs each)
    assert fp.cycle_ratio == pytest.approx(6 / 9)


def test_acyclic_domain_keeps_zero() -> None:
    """A chain of imports inside one domain stays 0.0."""
    nodes = module_with_funcs("lib.x", "lib/x.py", 1) + module_with_funcs("lib.y", "lib/y.py", 1)
    edges = [Edge(id="i1", source="lib.x", target="lib.y", type=EdgeType.IMPORTS)]
    with _store(nodes, edges) as store:
        fp = FingerprintExtractor(store).extract("lib")
    assert fp.cycle_ratio == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Pipeline-ingested cycle integration test (#221 colleague refuted-finding)
# Guards the resolver-uplift assumption: raw IMPORTS targets get resolved to
# file-node ids so Tarjan SCC can find the cycle.
# ---------------------------------------------------------------------------


def test_pipeline_ingested_mutual_imports_produce_nonzero_cycle_ratio(
    tmp_path: Path,
) -> None:
    """Two real .py files with mutual imports → cycle_ratio > 0 after full pipeline.

    This is an integration-level check: the extractor emits raw IMPORTS edges,
    the resolver resolves them to file-node FQNs, and FingerprintExtractor.extract
    must see the cycle in the intra-domain IMPORTS subgraph.
    """
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "alpha.py").write_text(
        "from mypkg.beta import beta_val\nalpha_val = 1\n", encoding="utf-8"
    )
    (pkg / "beta.py").write_text(
        "from mypkg.alpha import alpha_val\nbeta_val = 2\n", encoding="utf-8"
    )

    db_path = str(tmp_path / "graph.db")
    pipeline = IngestionPipeline({".py": PythonExtractor()})
    with SQLiteStore(db_path) as store:
        pipeline.run(str(tmp_path), store=store)

    with SQLiteStore(db_path) as store:
        fp = FingerprintExtractor(store).extract("mypkg")

    assert fp.cycle_ratio > 0, (
        "mypkg.alpha and mypkg.beta mutually import each other — "
        "cycle_ratio must be > 0 after pipeline ingestion with resolver uplift"
    )


# ── tangle_ratio property: worst-layer mutual-motif mass (#186) ───────────────

_ZEROS = tuple(0.0 for _ in TRIAD_ORDER)


def _onehot(name: str) -> tuple[float, ...]:
    return tuple(1.0 if t == name else 0.0 for t in TRIAD_ORDER)


def _tangle_fp(
    t_imports: tuple[float, ...] = _ZEROS,
    t_calls: tuple[float, ...] = _ZEROS,
    unresolved_ratio: float = 0.0,
) -> PatternFingerprint:
    return PatternFingerprint(
        domain="d",
        hub_count=0,
        star_count=0,
        chain_len=0.0,
        dag_depth=0,
        router_count=0,
        cycle_ratio=0.0,
        unresolved_ratio=unresolved_ratio,
        t_imports=t_imports,
        t_calls=t_calls,
    )


def test_tangle_ratio_zero_for_empty_census() -> None:
    assert _tangle_fp().tangle_ratio == pytest.approx(0.0)


def test_tangle_ratio_takes_worst_layer() -> None:
    # imports clean DAG (021C), calls pure mesh (300) → max picks 1.0.
    fp = _tangle_fp(t_imports=_onehot("021C"), t_calls=_onehot("300"))
    assert fp.tangle_ratio == pytest.approx(1.0)


def test_tangle_ratio_pure_dag_is_zero() -> None:
    fp = _tangle_fp(t_imports=_onehot("030T"), t_calls=_onehot("021C"))
    assert fp.tangle_ratio == pytest.approx(0.0)


# ── #244: CALLS-layer tangle faded by (1 - unresolved_ratio) ──────────────────


def test_tangle_ratio_discounts_calls_layer_by_unresolved() -> None:
    # calls = pure mesh (tangle 1.0), half the calls unresolved → discount 0.5.
    fp = _tangle_fp(t_calls=_onehot("300"), unresolved_ratio=0.5)
    assert fp.tangle_ratio == pytest.approx(0.5)


def test_tangle_ratio_fully_unresolved_calls_fade_to_zero() -> None:
    # all calls unresolved → discount 0 → calls-tangle cannot trip the gate.
    fp = _tangle_fp(t_calls=_onehot("300"), unresolved_ratio=1.0)
    assert fp.tangle_ratio == pytest.approx(0.0)


def test_tangle_ratio_imports_layer_is_not_discounted() -> None:
    # imports are always resolved — unresolved_ratio must not fade import tangle.
    fp = _tangle_fp(t_imports=_onehot("300"), unresolved_ratio=0.9)
    assert fp.tangle_ratio == pytest.approx(1.0)


def test_tangle_ratio_resolved_calls_unchanged() -> None:
    # unresolved_ratio 0 → discount 1 → calls-tangle unchanged (regression guard).
    fp = _tangle_fp(t_calls=_onehot("300"), unresolved_ratio=0.0)
    assert fp.tangle_ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Transparent re-export re-attribution in the IMPORTS census (#182)
# ---------------------------------------------------------------------------


def _mod(fqn: str, reexports: dict[str, str] | None = None) -> Node:
    """A FILE node, optionally carrying transparent re-exports."""
    return Node(
        id=fqn,
        type=NodeType.FILE,
        name=fqn.rsplit(".", maxsplit=1)[-1],
        file_path=fqn.replace(".", "/") + ".py",
        start_line=1,
        end_line=9,
        metadata={"reexports": reexports} if reexports else {},
    )


def _imports(source: str, target: str) -> Edge:
    return Edge(id=f"{source}:i:{target}", type=EdgeType.IMPORTS, source=source, target=target)


def _imports_symbol(source: str, target: str) -> Edge:
    return Edge(
        id=f"{source}:s:{target}", type=EdgeType.IMPORTS_SYMBOL, source=source, target=target
    )


def _census(nodes: list[Node], edges: list[Edge], prefix: str) -> dict[str, float]:
    """Non-zero IMPORTS triad shares, keyed by class name."""
    fp = FingerprintExtractor.from_graph(nodes, edges).extract(prefix)
    return {name: share for name, share in zip(TRIAD_ORDER, fp.t_imports, strict=True) if share}


#: The PR #180 shape: engine -> symbols -> indices, with symbols forwarding a
#: name it never uses from indices. The real dependency graph is a triangle.
_LAUNDERED_EDGES = [
    _imports("d.engine", "d.symbols"),
    _imports("d.symbols", "d.indices"),
    _imports_symbol("d.engine", "d.symbols.SymbolResolver"),
    _imports_symbol("d.engine", "d.symbols.IndexBuilder"),
]


def test_passthrough_reexport_measures_as_a_triangle() -> None:
    """The laundering from #180: a chain on paper, a triangle in truth."""
    nodes = [
        _mod("d.engine"),
        _mod("d.symbols", {"IndexBuilder": "d.indices.IndexBuilder"}),
        _mod("d.indices"),
    ]

    assert _census(nodes, _LAUNDERED_EDGES, "d") == {"030T": 1.0}


def test_same_shape_without_a_reexport_stays_a_chain() -> None:
    """Control: identical edges, no forwarding declared — nothing is re-attributed."""
    nodes = [_mod("d.engine"), _mod("d.symbols"), _mod("d.indices")]

    assert _census(nodes, _LAUNDERED_EDGES, "d") == {"021C": 1.0}


def test_reattribution_does_not_reach_outside_the_domain() -> None:
    """A facade forwarding from another domain is API surface, not laundering.

    Punishing it would teach deep-importing internals to keep the dashboard
    green, so the definer must be in the same domain for the edge to be added.
    """
    nodes = [
        _mod("d.engine"),
        _mod("d.facade", {"Thing": "other.core.Thing"}),
        _mod("d.indices"),
    ]
    edges = [
        _imports("d.engine", "d.facade"),
        _imports("d.facade", "d.indices"),
        _imports_symbol("d.engine", "d.facade.Thing"),
    ]

    assert _census(nodes, edges, "d") == {"021C": 1.0}


def test_reattribution_adds_rather_than_replaces() -> None:
    """The real A->B edge survives alongside the revealed A->C one.

    engine takes SymbolResolver (real) as well as IndexBuilder (forwarded);
    dropping engine->symbols would trade a hidden edge for a lost one.
    """
    nodes = [
        _mod("d.engine"),
        _mod("d.symbols", {"IndexBuilder": "d.indices.IndexBuilder"}),
        _mod("d.indices"),
    ]
    fp = FingerprintExtractor.from_graph(nodes, _LAUNDERED_EDGES).extract("d")

    # 030T requires all three edges: engine->symbols, symbols->indices, engine->indices.
    assert fp.t_imports[TRIAD_ORDER.index("030T")] == 1.0


def _reattributed_targets(nodes: list[Node], edges: list[Edge], importer: str) -> set[str]:
    """IMPORTS targets seen by the census for `importer`, including revealed ones."""
    domain_ids = {n.id for n in nodes}
    internal = [e for e in edges if e.source in domain_ids and e.target in domain_ids]
    out = _reattributed_imports(nodes, domain_ids, internal, edges)
    return {e.target for e in out if e.type == EdgeType.IMPORTS and e.source == importer}


def test_chained_reexports_are_followed_to_the_definer() -> None:
    """Two passthroughs in a row must not be a bypass.

    A -> B -> C -> D where both B and C only forward. Asserting on the edge set
    rather than a triad share: stopping at C still produces a 030T (from the
    a-b-c triangle), so a share-based check passes for the wrong reason.
    """
    nodes = [
        _mod("d.a"),
        _mod("d.b", {"Thing": "d.c.Thing"}),
        _mod("d.c", {"Thing": "d.dd.Thing"}),
        _mod("d.dd"),
    ]
    edges = [
        _imports("d.a", "d.b"),
        _imports("d.b", "d.c"),
        _imports("d.c", "d.dd"),
        _imports_symbol("d.a", "d.b.Thing"),
    ]

    assert "d.dd" in _reattributed_targets(nodes, edges, "d.a")


def test_reexported_module_resolves_to_the_module_not_its_package() -> None:
    """`from pkg import submodule as submodule` forwards a module, not a symbol.

    Taking the parent of the forwarded FQN would name the package; the import
    actually lands on the submodule.
    """
    nodes = [_mod("d.a"), _mod("d.facade", {"leaf": "d.leaf"}), _mod("d.leaf")]
    edges = [
        _imports("d.a", "d.facade"),
        _imports("d.facade", "d.leaf"),
        _imports_symbol("d.a", "d.facade.leaf"),
    ]

    assert "d.leaf" in _reattributed_targets(nodes, edges, "d.a")


def test_reexport_cycle_terminates() -> None:
    """Two modules forwarding the same name to each other must not hang.

    The docstring claimed the visited set handles this; review pointed out it
    was never tested. Both the mutual and the self-referential shape resolve to
    the symbol they started from, which `_definer_for` then discards.
    """
    mutual = {"d.a": {"T": "d.b.T"}, "d.b": {"T": "d.a.T"}}
    assert _follow_forwarding(mutual, "d.a.T") == "d.a.T"

    self_ref = {"d.a": {"T": "d.a.T"}}
    assert _follow_forwarding(self_ref, "d.a.T") == "d.a.T"


def test_dotless_symbol_is_not_a_forward() -> None:
    """A top-level name has no `via` part, so it cannot be a passthrough."""
    assert _follow_forwarding({"d.a": {"T": "d.b.T"}}, "Thing") is None
