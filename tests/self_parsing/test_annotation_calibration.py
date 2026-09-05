"""Self-parse calibration for annotation extraction (spec D3, D9).

Counts are measured against HEAD at the time PR1 was written. They are a
ratchet, not a constant: a change that legitimately moves them should update
the numbers in the same commit, with the new measurement in the message.
"""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.storage.sqlite_store import SQLiteStore

_GraphData = tuple[SQLiteStore, list[Node], list[Edge]]

# Measured on HEAD (852fa3d), 2026-09-05, via:
#   uv run cgis ingest src --source-root src --output /tmp/pr1-check.db
#   sqlite3 /tmp/pr1-check.db "select count(*) from edges where type='REFERENCES';"
# Controller Ruling 9 predicted 584 from a snapshot taken at 53d3811, one commit
# before HEAD. 852fa3d then deleted resolve_internal_class(resolver:
# SymbolResolver, ...) from symbols.py — that function's own first-parameter
# annotation named an internal class, so it was itself producing one of the
# counted REFERENCES edges. Deleting the function removed that annotation from
# the measured source; no resolver behaviour changed. 584 was correct at
# 53d3811, 583 is correct at HEAD — one internal-class annotation fewer, one
# fewer edge.
_EXPECTED_REFERENCES = 583
_TOLERANCE = 60  # ~10%: absorbs ordinary code churn, not a lost source


def test_reference_edge_count_is_within_the_calibrated_band(root_graph_data: _GraphData) -> None:
    """A lost annotation position or a regression to the cleaned head fails here."""
    _store, _nodes, edges = root_graph_data
    refs = [e for e in edges if e.type == EdgeType.REFERENCES]
    assert abs(len(refs) - _EXPECTED_REFERENCES) <= _TOLERANCE, (
        f"REFERENCES edges = {len(refs)}, expected {_EXPECTED_REFERENCES}±{_TOLERANCE}. "
        "Re-measure with a fresh ingest before changing this number; the repo-root "
        "graph.db is stale and must not be used."
    )


def test_every_reference_target_is_an_internal_class(root_graph_data: _GraphData) -> None:
    """D3: stdlib, third-party and unresolved annotations must not produce edges."""
    _store, nodes, edges = root_graph_data
    classes = {n.id for n in nodes if n.type == NodeType.CLASS}
    ref_targets = {e.target for e in edges if e.type == EdgeType.REFERENCES}
    assert ref_targets, "no REFERENCES edges produced at all — nothing to check here"
    bad = sorted(ref_targets - classes)
    assert not bad, f"REFERENCES edges pointing at non-class nodes: {bad[:10]}"


def test_classes_referenced_only_inside_generics_have_edges(root_graph_data: _GraphData) -> None:
    """D9's reason for existing: these 8 get zero edges under the cleaned-head rule.

    UnionRun is deliberately excluded: its only external-looking mention is
    ``-> "UnionRun"`` on its own classmethod, a self-reference the resolver
    correctly does not turn into an edge (Controller Ruling 9).
    """
    only_in_generics = {
        "AmbiguousEntry",
        "ArchitecturalAnomaly",
        "Bridge",
        "Community",
        "GoldenComment",
        "NodeMetric",
        "PrPlan",
        "SliceCounts",
    }
    _store, _nodes, edges = root_graph_data
    targets = {e.target.rsplit(".", maxsplit=1)[-1] for e in edges if e.type == EdgeType.REFERENCES}
    missing = sorted(only_in_generics - targets)
    assert not missing, f"classes referenced only inside generics have no edge: {missing}"


def test_classes_carry_self_types(root_graph_data: _GraphData) -> None:
    """D1: at least the classes measured to have annotated attributes have the map.

    Measured 75 on HEAD; the threshold of 70 leaves deliberate headroom for
    ordinary churn while still catching a regression that loses a large slice
    of attribute-type collection.
    """
    _store, nodes, _edges = root_graph_data
    with_map = [n for n in nodes if n.type == NodeType.CLASS and n.metadata.get("self_types")]
    assert len(with_map) >= 70, (
        f"only {len(with_map)} classes carry self_types (measured 75 on HEAD)"
    )
