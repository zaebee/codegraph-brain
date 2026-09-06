"""Self-parse calibration for annotation extraction (spec D3, D9).

Counts are measured against HEAD at the time PR1 was written. They are a
ratchet, not a constant: a change that legitimately moves them should update
the numbers in the same commit, with the new measurement in the message.
"""

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.extractors.python_extractor import PythonExtractor
from cgis.resolver.engine import ResolverEngine
from cgis.storage.sqlite_store import SQLiteStore

_GraphData = tuple[SQLiteStore, list[Node], list[Edge]]

# Measured on the final review fix wave (post 8ef9631), 2026-09-05, via:
#   uv run cgis ingest src --source-root src --output /tmp/final-check.db
#   sqlite3 /tmp/final-check.db "select count(*) from edges where type='REFERENCES';"
# 583 was correct at 8ef9631. Two fixes moved it to 585:
#   +decorated functions (@classmethod/@staticmethod/@property/@overload) now
#    get their return-annotation edge, same as plain functions (was silently
#    dropped by _handle_decorated_definition never calling
#    collect_return_annotation) — this adds edges, including a few that are
#    themselves self-references;
#   -a source-is-target self-reference (a method or nested class naming its
#    own enclosing class) is now dropped in _resolved_dep_edge, which the
#    spec's D9 paragraph always claimed but the code never implemented —
#    this includes both the newly-added decorated self-references and the
#    pre-existing ones (DuckDBAnalyzer.__enter__ -> DuckDBAnalyzer,
#    SQLiteStore.__enter__ -> SQLiteStore).
# Net across both fixes plus ordinary churn since 8ef9631: 583 -> 585.
# Two mechanisms now produce REFERENCES edges, and they are pinned separately.
# A single total would let one of them die while the other grows and still pass
# — the failure mode this file exists to catch. The id prefix tells them apart:
# `rawdep_` is an annotation position (D4/D9), `nameref_` is a name in a load
# position (D10).
_EXPECTED_ANNOTATION_REFERENCES = 594
_EXPECTED_NAME_REFERENCES = 172
_TOLERANCE = 60  # ~10% of the annotation band: ordinary code churn, not a lost source
_NAME_REF_TOLERANCE = 25  # the name-reference band is smaller, so its band is too


def _by_mechanism(edges: list[Edge]) -> tuple[list[Edge], list[Edge]]:
    """Split REFERENCES edges into (annotation, name-reference).

    Both halves test the id, rather than one testing membership in the other:
    `e not in name_refs` is a linear scan of Pydantic models per edge, which
    measured 60 s on the owner-api graph.
    """
    refs = [e for e in edges if e.type == EdgeType.REFERENCES]
    return (
        [e for e in refs if ":nameref_" not in e.id],
        [e for e in refs if ":nameref_" in e.id],
    )


def test_annotation_reference_count_is_within_the_calibrated_band(
    root_graph_data: _GraphData,
) -> None:
    """A lost annotation position or a regression to the cleaned head fails here."""
    _store, _nodes, edges = root_graph_data
    annotations, _ = _by_mechanism(edges)
    assert abs(len(annotations) - _EXPECTED_ANNOTATION_REFERENCES) <= _TOLERANCE, (
        f"annotation REFERENCES edges = {len(annotations)}, expected "
        f"{_EXPECTED_ANNOTATION_REFERENCES}±{_TOLERANCE}. Re-measure with a fresh ingest "
        "before changing this number; the repo-root graph.db is stale and must not be used."
    )


def test_name_reference_count_is_within_the_calibrated_band(root_graph_data: _GraphData) -> None:
    """A load position that stops being collected fails here (spec D10).

    Measured at the commit that introduced the rule. On cgis's own source this
    band is worth less than it looks — the orphan false-positive rate D10 fixes
    is 0% here and 33-40% on the application codebases it was measured against —
    so it guards against the mechanism dying, not against its precision.
    """
    _store, _nodes, edges = root_graph_data
    _, name_refs = _by_mechanism(edges)
    assert abs(len(name_refs) - _EXPECTED_NAME_REFERENCES) <= _NAME_REF_TOLERANCE, (
        f"name-reference REFERENCES edges = {len(name_refs)}, expected "
        f"{_EXPECTED_NAME_REFERENCES}±{_NAME_REF_TOLERANCE}. Re-measure with a fresh ingest "
        "before changing this number; the repo-root graph.db is stale and must not be used."
    )


def test_a_class_only_ever_named_as_a_value_is_still_referenced() -> None:
    """The gate with teeth: the shape D10 exists for, end to end (spec D10).

    The band above passes as long as *some* name references survive. This fails
    if the specific positions stop being collected — which is how the rule would
    actually regress, since each is a separate branch of `is_name_load`.
    """
    widget = "class Widget:\n    pass\n"
    user = (
        "from pkg.w import Widget\n\n"
        "REGISTRY = {'w': Widget}\n\n"
        "def go(app):\n"
        "    app.add_middleware(Widget)\n"
        "    try:\n"
        "        return Widget.SIZE\n"
        "    except Widget:\n"
        "        return None\n"
    )
    extractor = PythonExtractor()
    nodes_a, edges_a = extractor.parse(widget, "pkg/w.py")
    nodes_b, edges_b = extractor.parse(user, "pkg/user.py")
    resolved, _ = ResolverEngine(nodes_a + nodes_b, edges_a + edges_b).resolve()
    refs = {(e.source, e.target) for e in resolved if e.type == EdgeType.REFERENCES}
    assert ("pkg.user", "pkg.w.Widget") in refs, "collection literal at module level"
    assert ("pkg.user.go", "pkg.w.Widget") in refs, "argument, attribute head, except clause"


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

    Two classes are deliberately excluded, both for the same reason and
    neither needing D9's generic-unwrapping to explain their absence: their
    only external-looking mention is a self-reference, which the resolver's
    dedicated filter (spec D9, `_resolved_dep_edge` in `resolver/engine.py`)
    drops. `UnionRun.build` (a classmethod) returns ``-> "UnionRun"``, and
    `DuckDBAnalyzer.__enter__` returns ``-> "DuckDBAnalyzer"`` — neither name
    appears anywhere else in the source, generic-wrapped or otherwise, so
    both are genuine orphan candidates rather than D9 rescues.
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
