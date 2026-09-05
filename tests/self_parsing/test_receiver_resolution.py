"""Self-parse calibration for receiver resolution (#414, spec D7/D8).

Counts are measured against the tree at implementation time. They are a ratchet,
not a constant: a change that legitimately moves them updates the numbers in the
same commit, with the new measurement in the message.
"""

from cgis.core.models import Edge, Node
from cgis.storage.sqlite_store import SQLiteStore

# Measured via:
#   uv run cgis ingest src --source-root src --output /tmp/pr2.db
#   sqlite3 /tmp/pr2.db "select count(*) from edges where target like 'self.%';"
#
# Before this PR: 142 placeholder edges, 120 of them two-segment.
# After: 46 and 24. Of the 24 survivors, 13 have a genuinely unannotated
# receiver and 11 are declared as a builtin container (`self._profiles:
# dict[...]` records "dict", spec D1), which names no class. None is a
# resolution failure.
_EXPECTED_PLACEHOLDERS = 46
_TOLERANCE = 10


def test_self_placeholder_count_is_within_the_calibrated_band(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """Receiver resolution silently regressing shows up here as a rising count."""
    _store, _nodes, edges = root_graph_data
    placeholders = [e for e in edges if e.target.startswith("self.")]
    assert abs(len(placeholders) - _EXPECTED_PLACEHOLDERS) <= _TOLERANCE, (
        f"self.* placeholder edges = {len(placeholders)}, expected "
        f"{_EXPECTED_PLACEHOLDERS}±{_TOLERANCE}. Re-measure with a fresh ingest "
        "before changing this number; the repo-root graph.db is stale."
    )


def test_every_surviving_placeholder_has_a_reason(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """The sharp one: no two-segment placeholder may have a resolvable receiver.

    The count test above passes if resolution breaks for one class and starts
    working for another. This fails the moment a receiver that *is* declared as
    an internal class with that method stops resolving — which is the only way
    receiver resolution can regress without changing the total.
    """
    _store, nodes, edges = root_graph_data
    by_id = {n.id: n for n in nodes}
    self_types = {n.id: (n.metadata.get("self_types") or {}) for n in nodes}

    unexplained = []
    for edge in edges:
        parts = edge.target.split(".")
        if not edge.target.startswith("self.") or len(parts) != 3:
            continue
        attr, method = parts[1], parts[2]
        owner = edge.source.rsplit(".", maxsplit=1)[0]
        declared = self_types.get(owner, {}).get(attr)
        if declared is None:
            continue  # genuinely unannotated — the rule declines to guess (D1)
        if declared not in by_id:
            continue  # a builtin container or third-party type, no node (D1)
        if f"{declared}.{method}" not in by_id:
            continue  # the class exists but has no such method — a phantom (D7)
        unexplained.append((edge.source, edge.target))

    assert not unexplained, (
        "these placeholders name a receiver that resolves to a real class with "
        f"that method, so they should have resolved: {unexplained[:10]}"
    )


def test_no_two_segment_placeholder_is_shared_by_several_classes(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """The collision this PR removes, pinned on real code.

    Before: `self._parser.parse` was one graph vertex for PythonExtractor and
    TypeScriptExtractor, whose parsers are for different languages, and
    `self._store.get_all_nodes` merged FingerprintExtractor with
    SemanticUpliftEngine. Three such collisions existed; all three are gone.

    Two segments only, on purpose. A fourth collision at baseline,
    `self._pick_source_root`, has one segment: it is `self.<attr>()`, a call to
    a callable attribute. Resolving that would mean knowing which function was
    passed into the constructor — dataflow, not annotation — so D8 does not
    cover it and this PR must not be judged on it.
    """
    _store, _nodes, edges = root_graph_data
    owners: dict[str, set[str]] = {}
    for edge in edges:
        if edge.target.startswith("self.") and len(edge.target.split(".")) == 3:
            owners.setdefault(edge.target, set()).add(edge.source.rsplit(".", maxsplit=1)[0])
    shared = {t: sorted(o) for t, o in owners.items() if len(o) > 1}
    assert not shared, f"two-segment placeholders still shared by several classes: {shared}"


def test_a_resolved_receiver_call_lands_on_a_node_that_exists(
    root_graph_data: tuple[SQLiteStore, list[Node], list[Edge]],
) -> None:
    """A resolved call must reach a real node, internal or virtual — never a fabricated FQN.

    The external half of D7 keeps a method that has no node of its own when the
    receiver is a library type, relying on the engine to mint a boundary node
    for it. This asserts that minting actually happened for every such target.
    """
    _store, nodes, edges = root_graph_data
    known = {n.id for n in nodes}
    dangling = sorted({e.target for e in edges if e.target not in known})
    assert not dangling, f"edges pointing at non-existent nodes: {dangling[:10]}"
