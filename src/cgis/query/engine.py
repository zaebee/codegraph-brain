"""Implement query engine for code graph."""

from collections import deque

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
        Transitve upstream traversal (who calls me?).
        If target_node_id changes, what else is impacted?
        """
        visited_nodes: dict[str, Node] = {}
        visited_edges: dict[str, Edge] = {}

        start_node = self.store.get_node(target_node_id)
        if not start_node:
            return [], []

        visited_nodes[target_node_id] = start_node
        discovered_ids = {target_node_id}
        queue = deque([(target_node_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            incoming = self.store.get_incoming_edges(current_id)
            for edge in incoming:
                if edge.id not in visited_edges:
                    visited_edges[edge.id] = edge

                source_id = edge.source
                if source_id not in visited_nodes:
                    discovered_ids.add(source_id)
                    queue.append((source_id, depth + 1))

        # Batch fetch all discovered nodes that aren't already in visited_nodes
        missing_ids = list(discovered_ids - set(visited_nodes.keys()))
        if missing_ids:
            for node in self.store.get_nodes(missing_ids):
                visited_nodes[node.id] = node

        return list(visited_nodes.values()), list(visited_edges.values())

    def get_flow_graph(
        self, start_node_id: str, max_depth: int = 5
    ) -> tuple[list[Node], list[Edge]]:
        """
        Transitive downstream traversal (who do I call?).
        Traces execution path starting from start_node_id.
        """
        visited_nodes: dict[str, Node] = {}
        visited_edges: dict[str, Edge] = {}

        start_node = self.store.get_node(start_node_id)
        if not start_node:
            return [], []

        visited_nodes[start_node_id] = start_node
        discovered_ids = {start_node_id}
        queue = deque([(start_node_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            outgoing = self.store.get_outgoing_edges(current_id)
            for edge in outgoing:
                if edge.id not in visited_edges:
                    visited_edges[edge.id] = edge

                target_id = edge.target
                if target_id not in visited_nodes:
                    discovered_ids.add(target_id)
                    queue.append((target_id, depth + 1))

        missing_ids = list(discovered_ids - set(visited_nodes.keys()))
        if missing_ids:
            for node in self.store.get_nodes(missing_ids):
                visited_nodes[node.id] = node

        return list(visited_nodes.values()), list(visited_edges.values())
