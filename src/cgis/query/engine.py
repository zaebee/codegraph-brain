"""Implement query engine for code graph."""

from collections import Counter
from collections.abc import Callable
from typing import NamedTuple

from cgis.core.coverage import TraversalCoverage, rank_unresolved
from cgis.core.models import Edge, EdgeType, Node, NodeNamespace, NodeType
from cgis.storage.sqlite_store import RAW_CALL_PREFIX, SQLiteStore

STRUCTURAL_EDGE_TYPES: frozenset[EdgeType] = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})
BEHAVIORAL_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    t for t in EdgeType if t not in STRUCTURAL_EDGE_TYPES
)


class TraversalResult(NamedTuple):
    """A traversal's subgraph together with the coverage of the calls around it (#201).

    ``coverage`` is None when it was not asked for, or when the traversal
    followed no CALLS edges: a zero it did not measure would read as "fully
    resolved".
    """

    nodes: list[Node]
    edges: list[Edge]
    coverage: TraversalCoverage | None


class _Walk(NamedTuple):
    """Raw BFS output, before node lookup and external pruning."""

    discovered: set[str]
    edges: dict[str, Edge]
    expanded: set[str]
    calls: dict[str, Edge]


def _source(edge: Edge) -> str:
    """The upstream end of an edge — the neighbour an impact traversal moves to."""
    return edge.source


def _target(edge: Edge) -> str:
    """The downstream end of an edge — the neighbour a flow traversal moves to."""
    return edge.target


def _reachable_from(
    start_id: str,
    edges: list[Edge],
    get_neighbor_id: Callable[[Edge], str],
) -> set[str]:
    """DFS reachability from start_id through filtered edges."""
    adj: dict[str, list[str]] = {}
    for e in edges:
        nbr = get_neighbor_id(e)
        frm = e.source if nbr == e.target else e.target
        adj.setdefault(frm, []).append(nbr)
    reachable: set[str] = {start_id}
    queue: list[str] = [start_id]
    while queue:
        curr = queue.pop()
        for nbr in adj.get(curr, []):
            if nbr not in reachable:
                reachable.add(nbr)
                queue.append(nbr)
    return reachable


def _prune_external(
    start_id: str,
    nodes: list[Node],
    visited_edges: dict[str, Edge],
    get_neighbor_id: Callable[[Edge], str],
) -> tuple[list[Node], list[Edge]]:
    """Remove non-INTERNAL nodes and prune internal nodes disconnected after that removal."""
    nodes = [n for n in nodes if n.namespace == NodeNamespace.INTERNAL or n.id == start_id]
    internal_ids = {n.id for n in nodes} | {start_id}
    filtered_edges = [
        e for e in visited_edges.values() if e.source in internal_ids and e.target in internal_ids
    ]
    reachable = _reachable_from(start_id, filtered_edges, get_neighbor_id)
    nodes = [n for n in nodes if n.id in reachable]
    return nodes, [e for e in filtered_edges if e.source in reachable and e.target in reachable]


def _edge_accepted(
    edge: Edge,
    allowed_edge_types: frozenset[EdgeType] | None,
    min_confidence: float | None,
) -> bool:
    """True when an edge passes the traversal filters: type allow-list and confidence floor."""
    if allowed_edge_types is not None and edge.type not in allowed_edge_types:
        return False
    return not (min_confidence is not None and edge.confidence < min_confidence)


#: Node types a call can name. Only these are matched against unresolved calls:
#: a module's or a file's name is not something anyone calls.
_CALLABLE_TYPES = frozenset({NodeType.FUNCTION, NodeType.METHOD, NodeType.CLASS})


def _follows_calls(allowed_edge_types: frozenset[EdgeType] | None) -> bool:
    """True when a traversal with this type allow-list crosses CALLS edges at all."""
    return allowed_edge_types is None or EdgeType.CALLS in allowed_edge_types


def _is_unresolved(target: str, known: dict[str, Node]) -> bool:
    """The `get_edge_stats` definition: a `raw_call:` target, no node, or an UNKNOWN one."""
    if target.startswith(RAW_CALL_PREFIX):
        return True
    node = known.get(target)
    return node is None or node.namespace == NodeNamespace.UNKNOWN


def _finish(
    start_id: str,
    nodes: list[Node],
    walk: _Walk,
    get_neighbor_id: Callable[[Edge], str],
    show_external: bool,
) -> tuple[list[Node], list[Edge]]:
    """Apply external pruning, if asked for, to a walk's looked-up nodes and edges."""
    if not show_external:
        return _prune_external(start_id, nodes, walk.edges, get_neighbor_id)
    return nodes, list(walk.edges.values())


class QueryEngine:
    """
    Performs graph traversals over the SQLite Code Graph.
    Enables Impact Analysis (upstream) and Flow Tracing (downstream).
    """

    def __init__(self, store: SQLiteStore) -> None:
        """Bind the query engine to an open SQLiteStore instance."""
        self.store = store

    def get_impact_graph(
        self,
        target_node_id: str,
        max_depth: int = 5,
        allowed_edge_types: frozenset[EdgeType] | None = None,
        show_external: bool = True,
        min_confidence: float | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """
        Transitive upstream traversal (who calls me?).
        If target_node_id changes, what else is impacted?
        """
        walk = self._bfs_traverse(
            target_node_id,
            self.store.get_incoming_edges_batch,
            _source,
            max_depth,
            allowed_edge_types,
            min_confidence,
        )
        nodes = self.store.get_nodes(list(walk.discovered))
        return _finish(target_node_id, nodes, walk, _source, show_external)

    def get_impact_result(
        self,
        target_node_id: str,
        max_depth: int = 5,
        allowed_edge_types: frozenset[EdgeType] | None = None,
        show_external: bool = True,
        min_confidence: float | None = None,
        with_coverage: bool = True,
    ) -> TraversalResult:
        """`get_impact_graph`, plus how many callers the graph may be failing to show (#201).

        An incoming edge only exists for a call that resolved, so the unresolved
        calls that belong to this answer are never on the traversed path. They
        are counted by name instead: unresolved calls whose called name matches
        a function, method or class of ours the traversal expanded. That is an
        upper bound — see ``CoverageBasis`` — and it is labelled as one.

        ``with_coverage=False`` skips the count and its query, for a caller that
        will not render it.
        """
        walk = self._bfs_traverse(
            target_node_id,
            self.store.get_incoming_edges_batch,
            _source,
            max_depth,
            allowed_edge_types,
            min_confidence,
        )
        nodes = self.store.get_nodes(list(walk.discovered))
        subgraph = _finish(target_node_id, nodes, walk, _source, show_external)
        if not (with_coverage and _follows_calls(allowed_edge_types)):
            return TraversalResult(*subgraph, None)
        # Our own callables only. An UNKNOWN root is itself an unresolved target,
        # its callers already on the path — matching its name found the same
        # edge twice (found in review).
        names = {
            n.name
            for n in nodes
            if n.id in walk.expanded
            and n.namespace == NodeNamespace.INTERNAL
            and n.type in _CALLABLE_TYPES
        }
        hidden = self.store.unknown_calls_named(names)
        total_hidden = sum(hidden.values())
        coverage = TraversalCoverage(
            basis="unresolved_calls_by_name",
            calls_examined=len(walk.calls) + total_hidden,
            calls_unresolved=total_hidden,
            top_unresolved=rank_unresolved(hidden),
        )
        return TraversalResult(*subgraph, coverage)

    def get_flow_graph(
        self,
        start_node_id: str,
        max_depth: int = 5,
        allowed_edge_types: frozenset[EdgeType] | None = None,
        show_external: bool = True,
        min_confidence: float | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """
        Transitive downstream traversal (who do I call?).
        Traces execution path starting from start_node_id.
        """
        walk = self._bfs_traverse(
            start_node_id,
            self.store.get_outgoing_edges_batch,
            _target,
            max_depth,
            allowed_edge_types,
            min_confidence,
        )
        nodes = self.store.get_nodes(list(walk.discovered))
        return _finish(start_node_id, nodes, walk, _target, show_external)

    def get_flow_result(
        self,
        start_node_id: str,
        max_depth: int = 5,
        allowed_edge_types: frozenset[EdgeType] | None = None,
        show_external: bool = True,
        min_confidence: float | None = None,
        with_coverage: bool = True,
    ) -> TraversalResult:
        """`get_flow_graph`, plus how many of the calls it passed resolved to nothing (#201).

        Counts every CALLS edge leaving an expanded node, including the ones a
        confidence floor or external pruning then removes from the answer — those
        are exactly the calls the answer is missing.

        ``with_coverage=False`` skips the count and the lookup of call targets,
        for a caller that will not render it.
        """
        walk = self._bfs_traverse(
            start_node_id,
            self.store.get_outgoing_edges_batch,
            _target,
            max_depth,
            allowed_edge_types,
            min_confidence,
        )
        if not (with_coverage and _follows_calls(allowed_edge_types)):
            nodes = self.store.get_nodes(list(walk.discovered))
            return TraversalResult(
                *_finish(start_node_id, nodes, walk, _target, show_external), None
            )
        call_targets = [e.target for e in walk.calls.values()]
        known = {n.id: n for n in self.store.get_nodes([*walk.discovered, *call_targets])}
        nodes = [n for n in known.values() if n.id in walk.discovered]
        unresolved = Counter(t for t in call_targets if _is_unresolved(t, known))
        coverage = TraversalCoverage(
            basis="unresolved_calls_made",
            calls_examined=len(call_targets),
            calls_unresolved=unresolved.total(),
            top_unresolved=rank_unresolved(unresolved),
        )
        return TraversalResult(
            *_finish(start_node_id, nodes, walk, _target, show_external), coverage
        )

    def get_structural_graph(
        self, target_id: str, max_depth: int = 5
    ) -> tuple[list[Node], list[Edge]]:
        """
        Structural hierarchy rooted at target_id (FILE → CLASS → METHOD).
        Traverses only CONTAINS and DECLARES edges — no call-graph noise.
        Delegates to a single recursive CTE query in the store.
        """
        return self.store.get_structural_subgraph(target_id, max_depth)

    def _bfs_traverse(
        self,
        start_id: str,
        get_edges_batch: Callable[[list[str]], list[Edge]],
        get_neighbor_id: Callable[[Edge], str],
        max_depth: int,
        allowed_edge_types: frozenset[EdgeType] | None = None,
        min_confidence: float | None = None,
    ) -> _Walk:
        """
        Level-by-level BFS. Fetches edges for the entire frontier in one
        batch query per level — O(depth) DB roundtrips instead of O(nodes).

        ``min_confidence`` prunes edges below the given confidence (e.g.
        unresolved ``raw_call:`` edges at 0.1) so the traversal never crosses
        them — low-confidence neighbours stay out of the result.

        Alongside the accepted edges it records what coverage needs (#201): the
        ids it expanded, and every CALLS edge fetched for them *before* the
        confidence floor. A floor hides an edge from the answer, not from the
        count of what the answer is missing. Nodes at the depth horizon are not
        expanded, so what lies past it is truncation, not a resolution gap.
        """
        discovered_ids: set[str] = {start_id}
        visited_edges: dict[str, Edge] = {}
        expanded: set[str] = set()
        calls: dict[str, Edge] = {}
        count_calls = _follows_calls(allowed_edge_types)
        current_frontier = [start_id]
        depth = 0

        while current_frontier and depth < max_depth:
            expanded.update(current_frontier)
            next_frontier: list[str] = []
            for edge in get_edges_batch(current_frontier):
                if count_calls and edge.type == EdgeType.CALLS:
                    calls[edge.id] = edge
                if not _edge_accepted(edge, allowed_edge_types, min_confidence):
                    continue
                visited_edges[edge.id] = edge
                neighbor_id = get_neighbor_id(edge)
                if neighbor_id not in discovered_ids:
                    discovered_ids.add(neighbor_id)
                    next_frontier.append(neighbor_id)
            current_frontier = next_frontier
            depth += 1

        return _Walk(discovered_ids, visited_edges, expanded, calls)
