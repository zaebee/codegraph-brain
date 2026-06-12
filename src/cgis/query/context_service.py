"""Orchestrate the GraphRAG context package for a focal FQN (#19).

This is the impure seam between the graph (``QueryEngine``/``SQLiteStore``), the
filesystem (``snippet.py``) and the pure renderer (``prompt.py``) — mirroring the
``drift_service`` split. It fetches the focal node's direct CALLS neighbourhood
and enclosing class, reads its source, and hands everything to
``compile_context``.

Callers/callees are restricted to CALLS edges so structural noise
(CONTAINS/DECLARES) stays out of the "who calls / what it calls" lists; the
enclosing class is recovered separately from the structural layer.
"""

from pathlib import Path

from cgis.core.models import EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import QueryEngine
from cgis.query.prompt import compile_context
from cgis.query.snippet import extract_snippet
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

_CALLS: frozenset[EdgeType] = frozenset({EdgeType.CALLS})
_STRUCTURAL: frozenset[EdgeType] = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})


def _sorted_neighbours(nodes: list[Node], focus_fqn: str) -> list[Node]:
    """Keep only INTERNAL neighbours (drop self/builtins/third-party), ordered by location.

    Resolved EXTERNAL/STDLIB calls (``len``, ``pathlib.Path``, ``ValueError`` …)
    are pure noise in an agent context — the model already knows them — so the
    lists carry only repo-internal nodes. Truly unknown calls still surface via
    the unresolved ``raw_call:`` list.
    """
    return sorted(
        (n for n in nodes if n.id != focus_fqn and n.namespace == NodeNamespace.INTERNAL),
        key=lambda n: (n.file_path, n.start_line, n.id),
    )


def _direct_callers(engine: QueryEngine, focus_fqn: str, depth: int) -> list[Node]:
    """Upstream CALLS neighbours — who reaches the focal node (impact ripple)."""
    nodes, _ = engine.get_impact_graph(focus_fqn, max_depth=depth, allowed_edge_types=_CALLS)
    return _sorted_neighbours(nodes, focus_fqn)


def _direct_callees(
    engine: QueryEngine, focus_fqn: str, depth: int
) -> tuple[list[Node], list[str]]:
    """Downstream CALLS neighbours, split into resolved nodes and unresolved names."""
    nodes, edges = engine.get_flow_graph(focus_fqn, max_depth=depth, allowed_edge_types=_CALLS)
    resolved = _sorted_neighbours(nodes, focus_fqn)
    unresolved = sorted(
        {
            edge.target[len(RAW_CALL_PREFIX) :]
            for edge in edges
            if edge.source == focus_fqn and edge.target.startswith(RAW_CALL_PREFIX)
        }
    )
    return resolved, unresolved


def _structural_parent(store: SQLiteStore, focus_fqn: str) -> Node | None:
    """Return the node that structurally contains/declares the focal node, if any."""
    for edge in store.get_incoming_edges(focus_fqn):
        if edge.type in _STRUCTURAL:
            return store.get_node(edge.source)
    return None


def _class_context(store: SQLiteStore, focus: Node) -> tuple[Node | None, list[Node]]:
    """Recover the enclosing class and its sibling members (empty for free functions)."""
    parent = _structural_parent(store, focus.id)
    if parent is None or parent.type != NodeType.CLASS:
        return None, []
    members, _ = store.get_structural_subgraph(parent.id, 1)
    siblings = _sorted_neighbours([n for n in members if n.id != parent.id], focus.id)
    return parent, siblings


def build_context(store: SQLiteStore, focus_fqn: str, depth: int = 2, source_root: str = "") -> str:
    """Compile the agent-facing context package for an already-resolved ``focus_fqn``.

    ``depth`` controls how far the CALLS traversal reaches (1 = direct
    neighbours; 2 = the default, picking up one transitive hop of ripple/flow).
    A future adaptive strategy (#19 follow-up) will scale this by the node's
    out-degree — leaf nodes stay surgical, hubs pull deeper context.
    ``source_root`` is prepended to the
    node's stored (relative) ``file_path`` to locate the file on disk — needed
    when the graph was ingested from a sub-directory (e.g. ``cgis ingest ./src``
    stores ``cgis/...`` paths). Raises ``ValueError`` if the FQN is absent.
    """
    focus = store.get_node(focus_fqn)
    if focus is None:
        msg = f"FQN not found in graph: {focus_fqn}"
        raise ValueError(msg)
    engine = QueryEngine(store)
    file_path = str(Path(source_root) / focus.file_path) if source_root else focus.file_path
    source = extract_snippet(file_path, focus.start_line, focus.end_line)
    callers = _direct_callers(engine, focus_fqn, depth)
    callees, unresolved = _direct_callees(engine, focus_fqn, depth)
    class_node, siblings = _class_context(store, focus)
    return compile_context(
        focus=focus,
        source=source,
        class_node=class_node,
        siblings=siblings,
        callers=callers,
        callees=callees,
        unresolved_callees=unresolved,
    )
