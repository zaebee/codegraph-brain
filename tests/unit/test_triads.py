"""Tests for the 13-class connected triad census (spec §3.1/§3.2)."""

import pytest

from cgis.core.models import Edge, EdgeType
from cgis.query.triads import (
    _TANGLE_WEIGHTS,
    TRIAD_ORDER,
    ZERO_TRIADS,
    normalized_census,
    tangle_mass,
    triad_census,
    tv_distance,
)


def _e(source: str, target: str, etype: EdgeType = EdgeType.CALLS) -> Edge:
    return Edge(
        id=f"{source}:{etype.value}:{target}",
        source=source,
        target=target,
        type=etype,
        weight=1.0,
        confidence=1.0,
    )


def _census(edge_pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Census over nodes {a, b, c} with the given directed CALLS edges."""
    return triad_census({"a", "b", "c"}, [_e(s, t) for s, t in edge_pairs], EdgeType.CALLS)


def _single(edge_pairs: list[tuple[str, str]], expected: str) -> None:
    counts = _census(edge_pairs)
    assert counts[expected] == 1, counts
    assert sum(counts.values()) == 1, counts


# ── one hand-built graph per connected triad class ────────────────────────────


def test_021d_out_fork() -> None:
    """A←B→C: one node points at the two others."""
    _single([("b", "a"), ("b", "c")], "021D")


def test_021u_in_join() -> None:
    """A→B←C: two nodes point at one."""
    _single([("a", "b"), ("c", "b")], "021U")


def test_021c_path() -> None:
    """A→B→C directed path."""
    _single([("a", "b"), ("b", "c")], "021C")


def test_111d() -> None:
    """Mutual dyad plus an incoming edge: A↔B, C→A."""
    _single([("a", "b"), ("b", "a"), ("c", "a")], "111D")


def test_111u() -> None:
    """Mutual dyad plus an outgoing edge: A↔B, A→C."""
    _single([("a", "b"), ("b", "a"), ("a", "c")], "111U")


def test_030t_feed_forward() -> None:
    """Transitive triangle: A→B, B→C, A→C."""
    _single([("a", "b"), ("b", "c"), ("a", "c")], "030T")


def test_030c_cycle() -> None:
    """3-cycle: A→B→C→A."""
    _single([("a", "b"), ("b", "c"), ("c", "a")], "030C")


def test_201() -> None:
    """Two mutual dyads sharing a node: A↔B, B↔C."""
    _single([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")], "201")


def test_120d() -> None:
    """Mutual dyad, third node points at both: A↔B, C→A, C→B."""
    _single([("a", "b"), ("b", "a"), ("c", "a"), ("c", "b")], "120D")


def test_120u() -> None:
    """Mutual dyad, both members point at the third: A↔B, A→C, B→C."""
    _single([("a", "b"), ("b", "a"), ("a", "c"), ("b", "c")], "120U")


def test_120c() -> None:
    """Mutual dyad on a directed path: A↔B, C→A, B→C."""
    _single([("a", "b"), ("b", "a"), ("c", "a"), ("b", "c")], "120C")


def test_210() -> None:
    """Two mutual dyads plus one single edge: A↔B, B↔C, A→C."""
    _single([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("a", "c")], "210")


def test_300_complete() -> None:
    """All six directed edges present."""
    _single(
        [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("a", "c"), ("c", "a")],
        "300",
    )


# ── mechanics ─────────────────────────────────────────────────────────────────


def test_triad_order_has_13_classes() -> None:
    """The canonical order lists exactly the 13 connected triad classes."""
    assert len(TRIAD_ORDER) == 13
    assert len(set(TRIAD_ORDER)) == 13
    assert TRIAD_ORDER[0] == "021D"


def test_each_triple_counted_once() -> None:
    """A 4-node star b→{a,c,d} has 3 triads (one per pair from {a,c,d}, each sharing b)."""
    nodes = {"a", "b", "c", "d"}
    edges = [_e("b", "a"), _e("b", "c"), _e("b", "d")]
    counts = triad_census(nodes, edges, EdgeType.CALLS)
    assert counts["021D"] == 3
    assert sum(counts.values()) == 3


def test_self_loops_and_duplicate_edges_ignored() -> None:
    """Self-loops are dropped; a duplicated edge does not change the class."""
    edges = [_e("a", "a"), _e("a", "b"), _e("a", "b"), _e("b", "c")]
    counts = triad_census({"a", "b", "c"}, edges, EdgeType.CALLS)
    assert counts["021C"] == 1
    assert sum(counts.values()) == 1


def test_edges_outside_node_set_and_wrong_type_ignored() -> None:
    """Only intra-set edges of the requested type participate."""
    edges = [
        _e("a", "b"),
        _e("b", "c", EdgeType.IMPORTS),  # wrong type
        _e("b", "x"),  # x outside the set
    ]
    counts = triad_census({"a", "b", "c"}, edges, EdgeType.CALLS)
    assert sum(counts.values()) == 0  # a→b alone is not a connected TRIPLE


def test_empty_graph_zero_census() -> None:
    """No edges → all-zero census; normalization stays all-zero (no NaN)."""
    counts = triad_census({"a", "b", "c"}, [], EdgeType.CALLS)
    assert sum(counts.values()) == 0
    assert normalized_census(counts) == (0.0,) * 13


def test_normalized_census_sums_to_one() -> None:
    """Normalized census of a non-empty graph sums to 1.0, ordered by TRIAD_ORDER."""
    counts = _census([("b", "a"), ("b", "c")])
    norm = normalized_census(counts)
    assert len(norm) == 13
    assert sum(norm) == pytest.approx(1.0)
    assert norm[TRIAD_ORDER.index("021D")] == pytest.approx(1.0)


# ── tv_distance ───────────────────────────────────────────────────────────────


def test_tv_identical_vectors_zero() -> None:
    """TV of a vector with itself is 0."""
    t = (1.0,) + (0.0,) * 12
    tv, contribs = tv_distance(t, t, (1.0,) * 13)
    assert tv == pytest.approx(0.0)
    assert all(c == pytest.approx(0.0) for _, c in contribs)


def test_tv_disjoint_unit_vectors_one() -> None:
    """TV between two disjoint distributions is 1 (the metric's upper bound)."""
    a = (1.0,) + (0.0,) * 12
    b = (0.0, 1.0) + (0.0,) * 11
    tv, _ = tv_distance(a, b, (1.0,) * 13)
    assert tv == pytest.approx(1.0)


def test_tv_decomposes_per_triad() -> None:
    """Each contribution is 0.5*w_i*|a_i-b_i| and they sum to the total."""
    a = (0.6, 0.4) + (0.0,) * 11
    b = (1.0,) + (0.0,) * 12
    weights = (1.0, 0.5) + (1.0,) * 11
    tv, contribs = tv_distance(a, b, weights)
    assert contribs[0] == (TRIAD_ORDER[0], pytest.approx(0.2))  # ½·1.0·0.4
    assert contribs[1] == (TRIAD_ORDER[1], pytest.approx(0.1))  # ½·0.5·0.4
    assert tv == pytest.approx(sum(c for _, c in contribs))


# ── tangle_mass: normalized transpose-fixed (mutual) motif mass (#186) ────────


def _one_hot(name: str) -> tuple[float, ...]:
    """A normalized census concentrated entirely on one triad class."""
    return tuple(1.0 if t == name else 0.0 for t in TRIAD_ORDER)


def test_tangle_mass_pure_dag_is_zero() -> None:
    # 021C and 030T are acyclic, M=0 → not tangle.
    assert tangle_mass(_one_hot("021C")) == 0.0
    assert tangle_mass(_one_hot("030T")) == 0.0


def test_tangle_mass_pure_mesh_is_one() -> None:
    # 300 is the full mutual triad, M=3 → 3/3 = 1.0.
    assert tangle_mass(_one_hot("300")) == 1.0


def test_tangle_mass_single_mutual_dyad() -> None:
    # 201 has M=2 → 2/3.
    assert tangle_mass(_one_hot("201")) == 2.0 / 3.0
    # 120C has M=1 → 1/3.
    assert tangle_mass(_one_hot("120C")) == 1.0 / 3.0


def test_tangle_mass_empty_census_is_zero() -> None:
    assert tangle_mass(ZERO_TRIADS) == 0.0


def test_tangle_mass_mixed_is_weighted_average() -> None:
    # Half 021C (M=0), half 300 (M=3) → (0.5*0 + 0.5*3)/3 = 0.5.
    half = tuple(0.5 if t in ("021C", "300") else 0.0 for t in TRIAD_ORDER)
    assert tangle_mass(half) == 0.5


def test_tangle_weights_align_with_triad_order() -> None:
    assert len(_TANGLE_WEIGHTS) == len(TRIAD_ORDER) == 13
    # The MAN first digit per class, derived from the name itself.
    expected = tuple(int(name[0]) for name in TRIAD_ORDER)
    assert expected == _TANGLE_WEIGHTS
