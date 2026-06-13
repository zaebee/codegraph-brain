"""Reachability/coverage audit — does every source reach a required checkpoint? (#172).

The headline use is **authorization coverage**: of all the route handlers, which
ones never transitively reach the ownership check (``verify_resource_ownership``)?
That's the IDOR-class gap that previously took manual ``impact`` diffing to find.

The shape generalizes — handlers that touch storage but never reach a validator,
mutations that never reach event tracking, routes that bypass the service layer.
Reachability follows **behavioral** edges (CALLS, REFERENCES, DEPENDS_ON,
AUTHORIZES, …), so a checkpoint wired via FastAPI ``Depends()`` (a DEPENDS_ON
edge, #161) counts — exactly as it does at runtime.
"""

from dataclasses import dataclass

from cgis.core.models import EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import BEHAVIORAL_EDGE_TYPES, QueryEngine
from cgis.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class NodeRef:
    """A located reference to a graph node — enough to jump to it in an editor."""

    fqn: str
    file: str
    line: int


@dataclass(frozen=True)
class ReachabilityAudit:
    """Audit outcome: which sources reach ``target`` and which don't."""

    target: str
    covered: list[NodeRef]
    gaps: list[NodeRef]


def _prefix_matches(fqn: str, prefix: str) -> bool:
    """Dot-boundary prefix match — ``app.routes`` matches ``app.routes.x``, not ``app.routesX``."""
    return fqn == prefix or fqn.startswith(f"{prefix}.")


def _select_sources(
    store: SQLiteStore, from_type: NodeType | None, from_prefix: str | None
) -> list[Node]:
    """Select INTERNAL source nodes by node type and/or dot-boundary FQN prefix (AND-combined).

    Restricted to INTERNAL nodes so the audit never flags stdlib/third-party
    code, and the prefix is boundary-aware so a name overlap (``app.routesX``)
    can't sneak into an ``app.routes`` selection.
    """
    return [
        node
        for node in store.get_all_nodes()
        if node.namespace == NodeNamespace.INTERNAL
        and (from_type is None or node.type == from_type)
        and (from_prefix is None or _prefix_matches(node.id, from_prefix))
    ]


def _ref(node: Node) -> NodeRef:
    """Build a located reference from a node."""
    return NodeRef(fqn=node.id, file=node.file_path, line=node.start_line)


def audit_reachability(
    store: SQLiteStore,
    *,
    target_fqn: str,
    from_type: NodeType | None = None,
    from_prefix: str | None = None,
    max_depth: int = 5,
    allowed_edge_types: frozenset[EdgeType] | None = None,
) -> ReachabilityAudit:
    """Split the selected sources into those that reach ``target_fqn`` and those that don't.

    Sources are chosen by ``from_type`` and/or ``from_prefix`` (at least one is
    required; an empty/whitespace ``from_prefix`` is treated as unset). Coverage
    is decided by a **single upstream traversal** from the checkpoint
    (``get_impact_graph``) up to ``max_depth`` over ``allowed_edge_types``
    (behavioral edges by default): every node that reaches the checkpoint within
    the depth is in that set, so a source is *covered* iff it appears there,
    otherwise a *gap*. One BFS for the whole audit, not one per source. The
    checkpoint node itself is never audited as its own source.

    Note on ``target_fqn`` granularity: reaching a node means a behavioral edge
    *to that node*. For a checkpoint that callers invoke (a FUNCTION/METHOD like
    ``verify_resource_ownership``) this is exactly right. Pointing it at a CLASS
    measures *instantiation* (a call to the constructor), NOT method use on an
    already-injected instance — so a dependency-injected collaborator looks like
    a gap. Target the specific method (or the constructor) when auditing "does X
    use this collaborator".
    """
    from_prefix = from_prefix.strip() or None if from_prefix is not None else None
    if from_type is None and from_prefix is None:
        msg = "audit_reachability requires from_type or a non-empty from_prefix to select sources."
        raise ValueError(msg)
    engine = QueryEngine(store)
    edge_types = allowed_edge_types or BEHAVIORAL_EDGE_TYPES
    upstream_nodes, _ = engine.get_impact_graph(
        target_fqn, max_depth=max_depth, allowed_edge_types=edge_types
    )
    reaching = {node.id for node in upstream_nodes}
    covered: list[NodeRef] = []
    gaps: list[NodeRef] = []
    for source in sorted(_select_sources(store, from_type, from_prefix), key=lambda n: n.id):
        if source.id == target_fqn:
            continue
        (covered if source.id in reaching else gaps).append(_ref(source))
    return ReachabilityAudit(target=target_fqn, covered=covered, gaps=gaps)
