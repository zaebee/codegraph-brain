"""Self-parse calibration for receiver resolution (#414, spec D7/D8).

Counts are measured against the tree at implementation time. They are a ratchet,
not a constant: a change that legitimately moves them updates the numbers in the
same commit, with the new measurement in the message.
"""

from cgis.core.models import Edge, EdgeType, Node, NodeType
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


def _parent_classes(edges: list[Edge]) -> dict[str, list[str]]:
    """class FQN -> its resolved parents, from EXTENDS edges."""
    parents: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type is EdgeType.EXTENDS:
            parents.setdefault(edge.source, []).append(edge.target)
    return parents


def _owning_class(source_fqn: str, by_id: dict[str, Node]) -> str | None:
    """The nearest enclosing CLASS, mirroring the resolver's own walk.

    `rsplit(".", 1)` is not enough: a call inside a nested function has a source
    like `Cls.method.inner`, and taking one segment off yields `Cls.method`,
    which is in no self_types map — so a check built on it would skip exactly
    the sources whose owner the resolver has to work to find.
    """
    parts = source_fqn.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        node = by_id.get(candidate)
        if node is not None and node.type is NodeType.CLASS:
            return candidate
    return None


def _has_method(
    class_fqn: str, method: str, by_id: dict[str, Node], parents: dict[str, list[str]]
) -> bool:
    """Direct or inherited, mirroring _resolve_method_on_class_hierarchy.

    Checking only `f"{class_fqn}.{method}"` would treat every inherited method
    as a phantom — blinding the gate to precisely the inheritance path D7 asks
    the resolver to walk.
    """
    seen: set[str] = set()
    stack = [class_fqn]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if f"{current}.{method}" in by_id:
            return True
        stack.extend(parents.get(current, []))
    return False


def _is_unexplained(
    edge: Edge,
    by_id: dict[str, Node],
    self_types: dict[str, dict[str, str]],
    parents: dict[str, list[str]],
) -> bool:
    """True when this placeholder names a receiver that should have resolved.

    Every `continue` below is a documented reason the resolver declines, so a
    True here means the resolver failed at something it claims to handle.
    """
    parts = edge.target.split(".")
    if not edge.target.startswith("self.") or len(parts) != 3:
        return False
    attr, method = parts[1], parts[2]
    owner = _owning_class(edge.source, by_id)
    if owner is None:
        return False  # not inside a class at all
    declared = self_types.get(owner, {}).get(attr)
    if declared is None:
        return False  # genuinely unannotated — the rule declines to guess (D1)
    if declared not in by_id:
        return False  # a builtin container or third-party type, no node (D1)
    return _has_method(declared, method, by_id, parents)


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
    parents = _parent_classes(edges)

    unexplained = [
        (edge.source, edge.target)
        for edge in edges
        if _is_unexplained(edge, by_id, self_types, parents)
    ]

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
