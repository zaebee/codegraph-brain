"""Reachability/coverage audit — does every source reach a required checkpoint? (#172).

The headline use is **authorization coverage**: of all the route handlers, which
ones never transitively reach the ownership check (``verify_resource_ownership``)?
That's the IDOR-class gap that previously took manual ``impact`` diffing to find.

The shape generalizes — handlers that touch storage but never reach a validator,
mutations that never reach event tracking, routes that bypass the service layer.

Reachability follows **enforcement** edges by default — invocation (CALLS) and
runtime wiring (DEPENDS_ON, AUTHORIZES) — *not* every behavioral edge. This is
deliberate and load-bearing for a security primitive: merely *importing* or
*referencing* the guard (IMPORTS/IMPORTS_SYMBOL/REFERENCES) is not enforcing it,
so counting those as coverage would hide real IDOR gaps (a false "covered" is the
dangerous direction). Callers can pass a wider ``allowed_edge_types`` explicitly.
A guard wired via FastAPI ``Depends()`` (a DEPENDS_ON edge, #161) counts — *when
the resolver uplifts that wiring to the guard node*; an unresolved dynamic
provider (``raw_dep:``/``raw_call:`` at confidence 0.1) can't be proven and shows
as a gap.
"""

from dataclasses import dataclass

from cgis.core.models import EdgeType, Node, NodeNamespace, NodeType
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

# Edges that mean the source *enforces* (invokes / wires) the checkpoint, as
# opposed to merely importing or naming it. The default for an authz audit:
# false coverage (import-only) is worse than a false gap for a security linter.
_ENFORCEMENT_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.CALLS, EdgeType.DEPENDS_ON, EdgeType.AUTHORIZES}
)


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


class NoAuditSourcesError(ValueError):
    """The selectors matched no source to audit, so there is no result to report (#467).

    Raised instead of returning empty ``covered``/``gaps``: callers — and CI gates
    reading ``gaps == []`` — would otherwise take an audit of nothing for a clean one.
    """


_SUGGESTION_LIMIT = 5


def _describe_selection(from_type: NodeType | None, from_prefix: str | None) -> str:
    """Render the selectors the way a caller passed them, for the error message."""
    parts = []
    if from_type is not None:
        parts.append(f"from_type={from_type.value}")
    if from_prefix is not None:
        parts.append(f"from_prefix={from_prefix!r}")
    return " and ".join(parts)


def _whole_segment_prefixes(store: SQLiteStore, from_prefix: str) -> list[str]:
    """Dot-complete prefixes that extend ``from_prefix`` — what a partial segment meant."""
    completions: set[str] = set()
    for node in store.get_all_nodes():
        if node.namespace != NodeNamespace.INTERNAL or not node.id.startswith(from_prefix):
            continue
        next_dot = node.id.find(".", len(from_prefix))
        completions.add(node.id if next_dot == -1 else node.id[:next_dot])
    return sorted(completions)[:_SUGGESTION_LIMIT]


def _no_sources_error(
    store: SQLiteStore, from_type: NodeType | None, from_prefix: str | None
) -> NoAuditSourcesError:
    """Build the error for an empty selection, naming prefixes that would have matched."""
    msg = f"No sources matched {_describe_selection(from_type, from_prefix)} — nothing was audited."
    if from_prefix is not None:
        suggestions = _whole_segment_prefixes(store, from_prefix)
        if suggestions:
            msg += (
                " Prefixes match whole dot-segments; did you mean: " + ", ".join(suggestions) + "?"
            )
    return NoAuditSourcesError(msg)


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
    (**enforcement** edges — CALLS/DEPENDS_ON/AUTHORIZES — by default, so an
    import-only or reference-only link never counts as coverage): every node that
    reaches the checkpoint within the depth is in that set, so a source is
    *covered* iff it appears there, otherwise a *gap*. One BFS for the whole
    audit, not one per source.

    ``max_depth`` bounds the proof: a source that only reaches the checkpoint via
    a chain longer than ``max_depth`` is reported as a gap even though it is
    covered at runtime — raise it for deep route→service→crud→…→guard stacks. The
    checkpoint node itself is never audited as its own source.

    Note on ``target_fqn`` granularity: reaching a node means a behavioral edge
    *to that node*. For a checkpoint that callers invoke (a FUNCTION/METHOD like
    ``verify_resource_ownership``) this is exactly right. Pointing it at a CLASS
    measures *instantiation* (a call to the constructor), NOT method use on an
    already-injected instance — so a dependency-injected collaborator looks like
    a gap. Target the specific method (or the constructor) when auditing "does X
    use this collaborator".

    Raises:
        NoAuditSourcesError: The selectors match no source other than the checkpoint
            itself. An empty ``covered``/``gaps`` pair would read as a passing audit.
    """
    from_prefix = from_prefix.strip() or None if from_prefix is not None else None
    if from_type is None and from_prefix is None:
        msg = "audit_reachability requires from_type or a non-empty from_prefix to select sources."
        raise ValueError(msg)
    engine = QueryEngine(store)
    # `is not None`, not `or`: an explicit empty frozenset (no-traversal, target-only)
    # is a valid intent and must not be overridden by the enforcement default.
    edge_types = allowed_edge_types if allowed_edge_types is not None else _ENFORCEMENT_EDGE_TYPES
    upstream_nodes, _ = engine.get_impact_graph(
        target_fqn, max_depth=max_depth, allowed_edge_types=edge_types
    )
    sources = [s for s in _select_sources(store, from_type, from_prefix) if s.id != target_fqn]
    if not sources:
        raise _no_sources_error(store, from_type, from_prefix)
    reaching = {node.id for node in upstream_nodes}
    covered: list[NodeRef] = []
    gaps: list[NodeRef] = []
    for source in sorted(sources, key=lambda n: n.id):
        (covered if source.id in reaching else gaps).append(_ref(source))
    return ReachabilityAudit(target=target_fqn, covered=covered, gaps=gaps)
