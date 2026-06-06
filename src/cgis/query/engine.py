"""Implement query engine for code graph."""

from collections.abc import Callable

from cgis.core.models import Edge, Node
from cgis.storage.sqlite_store import SQLiteStore


class QueryEngine:
    """
    Performs graph traversals over the SQLite Code Graph.
    Enables Impact Analysis (upstream) and Flow Tracing (downstream).
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def get_impact_graph(
        self, target_node_id: str, max_depth: int = 5
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
        )

    def get_flow_graph(
        self, start_node_id: str, max_depth: int = 5
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
        )

    def _bfs_traverse(
        self,
        start_id: str,
        get_edges_batch: Callable[[list[str]], list[Edge]],
        get_neighbor_id: Callable[[Edge], str],
        max_depth: int,
    ) -> tuple[list[Node], list[Edge]]:
        """
        Level-by-level BFS. Fetches edges for the entire frontier in one
        batch query per level — O(depth) DB roundtrips instead of O(nodes).
        """
        discovered_ids: set[str] = {start_id}
        visited_edges: dict[str, Edge] = {}
        current_frontier = [start_id]
        depth = 0

        while current_frontier and depth < max_depth:
            edges = get_edges_batch(current_frontier)
            next_frontier: list[str] = []
            for edge in edges:
                visited_edges[edge.id] = edge
                neighbor_id = get_neighbor_id(edge)
                if neighbor_id not in discovered_ids:
                    discovered_ids.add(neighbor_id)
                    next_frontier.append(neighbor_id)
            current_frontier = next_frontier
            depth += 1

        nodes = self.store.get_nodes(list(discovered_ids))
        return nodes, list(visited_edges.values())
