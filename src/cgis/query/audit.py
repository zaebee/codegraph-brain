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

from cgis.core.models import EdgeType, Node, NodeType
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


def _select_sources(
    store: SQLiteStore, from_type: NodeType | None, from_prefix: str | None
) -> list[Node]:
    """Select audited source nodes by node type and/or FQN prefix (AND-combined)."""
    return [
        node
        for node in store.get_all_nodes()
        if (from_type is None or node.type == from_type)
        and (from_prefix is None or node.id.startswith(from_prefix))
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
    required). For each, a downstream traversal (``get_flow_graph``) up to
    ``max_depth`` over ``allowed_edge_types`` (behavioral edges by default) decides
    coverage: the source is *covered* if ``target_fqn`` is reachable, otherwise a
    *gap*. The checkpoint node itself is never audited as its own source.
    """
    if from_type is None and from_prefix is None:
        msg = "audit_reachability requires from_type or from_prefix to select sources."
        raise ValueError(msg)
    engine = QueryEngine(store)
    edge_types = allowed_edge_types or BEHAVIORAL_EDGE_TYPES
    covered: list[NodeRef] = []
    gaps: list[NodeRef] = []
    for source in sorted(_select_sources(store, from_type, from_prefix), key=lambda n: n.id):
        if source.id == target_fqn:
            continue
        reached_nodes, _ = engine.get_flow_graph(
            source.id, max_depth=max_depth, allowed_edge_types=edge_types
        )
        reached = {node.id for node in reached_nodes}
        (covered if target_fqn in reached else gaps).append(_ref(source))
    return ReachabilityAudit(target=target_fqn, covered=covered, gaps=gaps)
