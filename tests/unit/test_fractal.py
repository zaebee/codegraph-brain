"""Unit tests for the structural tier ladder and its entropy slope (#186)."""

import json
from pathlib import Path

from cgis.api.mcp_server import cgis_fractal
from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query.drift.fractal import (
    RungReport,
    analyze_fractal,
    build_ladder,
    entropy_bits,
    fit_ladder,
    measure_layer,
    verdict_of,
)
from cgis.query.drift.triads import TRIAD_ORDER, ZERO_TRIADS
from cgis.storage.sqlite_store import SQLiteStore


def _node(fqn: str, ntype: NodeType, path: str) -> Node:
    """A graph node for ladder tests."""
    return Node(
        id=fqn,
        type=ntype,
        name=fqn.rsplit(".", 1)[-1],
        file_path=path,
        start_line=0,
        end_line=0,
    )


def _edge(src: str, tgt: str, etype: EdgeType) -> Edge:
    """A graph edge for ladder tests."""
    return Edge(
        id=f"{src}:{etype.value}:{tgt}",
        source=src,
        target=tgt,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def test_t1_class_folds_method_into_declaring_class() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.Thing", NodeType.CLASS, "pkg/mod.py"),
        _node("pkg.mod.Thing.run", NodeType.METHOD, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.Thing", EdgeType.CONTAINS),
        _edge("pkg.mod.Thing", "pkg.mod.Thing.run", EdgeType.DECLARES),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.Thing.run"] == "pkg.mod.Thing"
    assert ladder["T2_module"]["pkg.mod.Thing.run"] == "pkg.mod"


def test_t1_class_leaves_module_level_function_on_its_file() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.helper", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [_edge("pkg.mod", "pkg.mod.helper", EdgeType.CONTAINS)]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.helper"] == "pkg.mod"


def test_nested_function_folds_through_its_enclosing_function() -> None:
    nodes = [
        _node("pkg.mod", NodeType.FILE, "pkg/mod.py"),
        _node("pkg.mod.outer", NodeType.FUNCTION, "pkg/mod.py"),
        _node("pkg.mod.outer.inner", NodeType.FUNCTION, "pkg/mod.py"),
    ]
    edges = [
        _edge("pkg.mod", "pkg.mod.outer", EdgeType.CONTAINS),
        _edge("pkg.mod.outer", "pkg.mod.outer.inner", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.mod.outer.inner"] == "pkg.mod"
    assert ladder["T2_module"]["pkg.mod.outer.inner"] == "pkg.mod"


def test_directory_rungs_trim_from_the_leaf_so_every_file_moves() -> None:
    """The regression test for the root-truncation artifact (#186).

    Trimming from the root end leaves shallow files stationary and produces
    near-duplicate rungs. Trimming from the leaf end moves every file at every
    rung until it bottoms out at <root>.
    """
    nodes = [
        _node("a.b.c.deep", NodeType.FILE, "a/b/c/deep.py"),
        _node("a.shallow", NodeType.FILE, "a/shallow.py"),
    ]

    ladder = dict(build_ladder(nodes, []))

    assert ladder["T3_up1"]["a.b.c.deep"] == "a/b/c"
    assert ladder["T3_up1"]["a.shallow"] == "a"
    assert ladder["T4_up2"]["a.b.c.deep"] == "a/b"
    assert ladder["T4_up2"]["a.shallow"] == "<root>"
    assert ladder["T5_up3"]["a.b.c.deep"] == "a"


def test_ladder_starts_with_the_identity_rung() -> None:
    nodes = [_node("pkg.mod", NodeType.FILE, "pkg/mod.py")]

    rungs = build_ladder(nodes, [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {"pkg.mod": "pkg.mod"}


def test_containment_cycle_does_not_hang() -> None:
    """A malformed graph must not spin the parent-walk forever."""
    nodes = [
        _node("pkg.a", NodeType.FUNCTION, "pkg/x.py"),
        _node("pkg.b", NodeType.FUNCTION, "pkg/x.py"),
    ]
    edges = [
        _edge("pkg.a", "pkg.b", EdgeType.CONTAINS),
        _edge("pkg.b", "pkg.a", EdgeType.CONTAINS),
    ]

    ladder = dict(build_ladder(nodes, edges))

    assert ladder["T1_class"]["pkg.a"] == "pkg.a"
    assert ladder["T1_class"]["pkg.b"] == "pkg.b"


def test_empty_graph_yields_no_rungs_beyond_identity() -> None:
    rungs = build_ladder([], [])

    assert rungs[0][0] == "T0_symbol"
    assert rungs[0][1] == {}


def _files(*paths: str) -> list[Node]:
    """FILE nodes named after their paths, dots for slashes."""
    return [_node(p.removesuffix(".py").replace("/", "."), NodeType.FILE, p) for p in paths]


def test_entropy_of_a_single_motif_is_zero() -> None:
    census = tuple(1.0 if name == "021C" else 0.0 for name in TRIAD_ORDER)

    assert entropy_bits(census) == 0.0


def test_entropy_of_two_equal_motifs_is_one_bit() -> None:
    census = tuple(0.5 if name in ("021C", "021D") else 0.0 for name in TRIAD_ORDER)

    assert entropy_bits(census) == 1.0


def test_identical_censuses_collapse_to_one_rung() -> None:
    """IMPORTS edges connect only FILE nodes, so T0/T1/T2 are the same quotient."""
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    rungs = measure_layer(nodes, edges, EdgeType.IMPORTS)

    assert [r.name for r in rungs] == ["T0_symbol", "T3_up1"]


def test_thin_rungs_are_reported_but_not_live() -> None:
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    rungs = measure_layer(nodes, edges, EdgeType.IMPORTS)

    assert rungs[0].triads == 1
    assert rungs[0].live is False
    assert rungs[0].dominant == "021C"


def test_a_rung_with_no_triads_has_no_entropy() -> None:
    nodes = _files("p/f1.py", "p/f2.py")

    rungs = measure_layer(nodes, [], EdgeType.CALLS)

    assert rungs[0].triads == 0
    assert rungs[0].entropy is None
    assert rungs[0].live is False


def _rung(name: str, groups: int, entropy: float) -> RungReport:
    """A live RungReport carrying only the two fields the fit reads."""
    return RungReport(
        name=name,
        groups=groups,
        triads=100,
        census=ZERO_TRIADS,
        entropy=entropy,
        dominant="021U",
        dominant_share=1.0,
        tangle_ratio=0.0,
        live=True,
    )


def test_rising_diversity_under_coarsening_is_hierarchical() -> None:
    rungs = [_rung("a", 800, 0.9), _rung("b", 400, 1.1), _rung("c", 200, 1.3), _rung("d", 100, 1.5)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert fit.slope > 0
    assert verdict_of(fit) == "hierarchical"


def test_collapsing_diversity_under_coarsening_is_flat() -> None:
    rungs = [_rung("a", 800, 1.5), _rung("b", 400, 1.1), _rung("c", 200, 0.7), _rung("d", 100, 0.3)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert fit.slope < 0
    assert verdict_of(fit) == "flat"


def test_constant_entropy_is_scale_invariant() -> None:
    rungs = [_rung("a", 800, 1.2), _rung("b", 400, 1.2), _rung("c", 200, 1.2)]

    assert verdict_of(fit_ladder(rungs)) == "scale_invariant"


def test_slope_inside_the_dead_band_is_scale_invariant() -> None:
    """A noisy near-zero trend must not be reported as a direction."""
    rungs = [_rung("a", 800, 1.0), _rung("b", 400, 1.4), _rung("c", 200, 1.0), _rung("d", 100, 1.4)]

    fit = fit_ladder(rungs)

    assert fit is not None
    assert abs(fit.slope) <= 2.0 * fit.std_error
    assert verdict_of(fit) == "scale_invariant"


def test_too_few_live_rungs_is_no_signal() -> None:
    rungs = [_rung("a", 800, 0.9), _rung("b", 400, 1.4)]

    assert fit_ladder(rungs) is None
    assert verdict_of(None) == "no_signal"


def test_dead_rungs_are_excluded_from_the_fit() -> None:
    live = [_rung("a", 800, 0.9), _rung("b", 400, 1.1), _rung("c", 200, 1.3)]
    dead = RungReport(
        name="top",
        groups=2,
        triads=1,
        census=ZERO_TRIADS,
        entropy=0.0,
        dominant="300",
        dominant_share=1.0,
        tangle_ratio=1.0,
        live=False,
    )

    fit = fit_ladder([*live, dead])

    assert fit is not None
    assert fit.live_rungs == 3


def test_verdict_survives_truncating_the_ladder() -> None:
    """Acceptance criterion 2: the verdict must not depend on rung count.

    This is the property whose absence retired the closure-gap metric — its
    ranking reshuffled completely when the grain was swept.
    """
    rungs = [
        _rung("a", 1600, 0.8),
        _rung("b", 800, 1.0),
        _rung("c", 400, 1.2),
        _rung("d", 200, 1.4),
        _rung("e", 100, 1.6),
    ]

    full = verdict_of(fit_ladder(rungs))

    assert full == "hierarchical"
    assert verdict_of(fit_ladder(rungs[1:])) == full
    assert verdict_of(fit_ladder(rungs[:-1])) == full
    assert verdict_of(fit_ladder(rungs[1:-1])) == full


def test_analyze_fractal_reports_both_the_curve_and_the_verdict() -> None:
    nodes = _files("p/f1.py", "p/f2.py", "p/f3.py")
    edges = [
        _edge("p.f1", "p.f2", EdgeType.IMPORTS),
        _edge("p.f2", "p.f3", EdgeType.IMPORTS),
    ]

    report = analyze_fractal(nodes, edges, EdgeType.IMPORTS)

    assert report.layer == "IMPORTS"
    assert report.verdict == "no_signal"
    assert [r.name for r in report.rungs] == ["T0_symbol", "T3_up1"]


def test_mcp_fractal_returns_json_layers(tmp_path: Path) -> None:
    db_path = str(tmp_path / "graph.db")
    with SQLiteStore(db_path) as store:
        store.save_graph(
            _files("p/f1.py", "p/f2.py", "p/f3.py"),
            [
                _edge("p.f1", "p.f2", EdgeType.IMPORTS),
                _edge("p.f2", "p.f3", EdgeType.IMPORTS),
            ],
        )

    payload = json.loads(cgis_fractal(db_path=db_path))

    assert [layer["layer"] for layer in payload["layers"]] == ["IMPORTS", "CALLS"]
    assert payload["layers"][0]["verdict"] == "no_signal"


def test_mcp_fractal_reports_a_missing_database(tmp_path: Path) -> None:
    assert "❌" in cgis_fractal(db_path=str(tmp_path / "nope.db"))
