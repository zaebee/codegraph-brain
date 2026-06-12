"""Implement query engine for code graph."""

from collections.abc import Callable

from cgis.core.models import Edge, EdgeType, Node, NodeNamespace
from cgis.storage.sqlite_store import SQLiteStore

STRUCTURAL_EDGE_TYPES: frozenset[EdgeType] = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})
BEHAVIORAL_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    t for t in EdgeType if t not in STRUCTURAL_EDGE_TYPES
)


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
        return self._bfs_traverse(
            target_node_id,
            self.store.get_incoming_edges_batch,
            lambda e: e.source,
            max_depth,
            allowed_edge_types=allowed_edge_types,
            show_external=show_external,
            min_confidence=min_confidence,
        )

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
        return self._bfs_traverse(
            start_node_id,
            self.store.get_outgoing_edges_batch,
            lambda e: e.target,
            max_depth,
            allowed_edge_types=allowed_edge_types,
            show_external=show_external,
            min_confidence=min_confidence,
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
        show_external: bool = True,
        min_confidence: float | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """
        Level-by-level BFS. Fetches edges for the entire frontier in one
        batch query per level — O(depth) DB roundtrips instead of O(nodes).

        ``min_confidence`` prunes edges below the given confidence (e.g.
        unresolved ``raw_call:`` edges at 0.1) so the traversal never crosses
        them — low-confidence neighbours stay out of the result.
        """
        discovered_ids: set[str] = {start_id}
        visited_edges: dict[str, Edge] = {}
        current_frontier = [start_id]
        depth = 0

        while current_frontier and depth < max_depth:
            edges = get_edges_batch(current_frontier)
            next_frontier: list[str] = []
            for edge in edges:
                if allowed_edge_types is not None and edge.type not in allowed_edge_types:
                    continue
                if min_confidence is not None and edge.confidence < min_confidence:
                    continue
                visited_edges[edge.id] = edge
                neighbor_id = get_neighbor_id(edge)
                if neighbor_id not in discovered_ids:
                    discovered_ids.add(neighbor_id)
                    next_frontier.append(neighbor_id)
            current_frontier = next_frontier
            depth += 1

        nodes = self.store.get_nodes(list(discovered_ids))

        if not show_external:
            return _prune_external(start_id, nodes, visited_edges, get_neighbor_id)

        return nodes, list(visited_edges.values())
