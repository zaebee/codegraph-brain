"""Per-node health metrics: fan_in, fan_out, depth, in_cycle."""

from collections import deque
from dataclasses import dataclass

from cgis.core.models import Edge, EdgeType, Node, NodeType
from cgis.query._scc import build_adjacency, tarjan_scc

_BEHAVIORAL = frozenset({EdgeType.CALLS, EdgeType.IMPORTS, EdgeType.REFERENCES, EdgeType.USES})
_STRUCTURAL = frozenset({EdgeType.CONTAINS, EdgeType.DECLARES})


@dataclass(frozen=True)
class NodeHealth:
    """Health metrics for a single node."""

    fan_in: int
    fan_out: int
    depth: int
    in_cycle: bool


class HealthScorer:
    """Compute health metrics for every node in a graph snapshot."""

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Accept the full node/edge lists from an ingestion run."""
        self._nodes = nodes
        self._edges = edges

    def _compute_fan_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """Count incoming and outgoing behavioral edges (CALLS, IMPORTS, REFERENCES, USES) per node.

        Returns fan_in and fan_out dicts keyed by node ID.
        """
        fan_in: dict[str, int] = {n.id: 0 for n in self._nodes}
        fan_out: dict[str, int] = {n.id: 0 for n in self._nodes}
        for edge in self._edges:
            if edge.type not in _BEHAVIORAL:
                continue
            if edge.source in fan_out:
                fan_out[edge.source] += 1
            if edge.target in fan_in:
                fan_in[edge.target] += 1
        return fan_in, fan_out

    def _compute_depths(self) -> dict[str, int]:
        """BFS from FILE nodes via CONTAINS/DECLARES to assign structural depth."""
        children: dict[str, list[str]] = {}
        for edge in self._edges:
            if edge.type in _STRUCTURAL:
                children.setdefault(edge.source, []).append(edge.target)

        file_ids = {n.id for n in self._nodes if n.type == NodeType.FILE}
        depths: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque((fid, 0) for fid in file_ids)
        while queue:
            node_id, d = queue.popleft()
            if node_id in depths:
                continue
            depths[node_id] = d
            for child_id in children.get(node_id, []):
                if child_id not in depths:
                    queue.append((child_id, d + 1))
        return depths

    def _compute_cycles(self) -> set[str]:
        """Return the set of FILE node IDs that participate in IMPORTS cycles."""
        file_ids = {n.id for n in self._nodes if n.type == NodeType.FILE}
        adj = build_adjacency(self._edges, frozenset({EdgeType.IMPORTS}))
        adj = {k: [v for v in vs if v in file_ids] for k, vs in adj.items() if k in file_ids}
        cyclic: set[str] = set()
        for scc in tarjan_scc(adj):
            if len(scc) > 1:
                cyclic.update(scc)
        return cyclic

    def _compute_file_ancestors(self) -> dict[str, str]:
        """Map every descendant node ID to the FILE node it belongs to (transitive)."""
        children: dict[str, list[str]] = {}
        for edge in self._edges:
            if edge.type in _STRUCTURAL:
                children.setdefault(edge.source, []).append(edge.target)

        file_ids = {n.id for n in self._nodes if n.type == NodeType.FILE}
        ancestor: dict[str, str] = {}
        queue: deque[tuple[str, str]] = deque((fid, fid) for fid in file_ids)
        while queue:
            node_id, root_file = queue.popleft()
            if node_id in ancestor:
                continue
            ancestor[node_id] = root_file
            for child_id in children.get(node_id, []):
                if child_id not in ancestor:
                    queue.append((child_id, root_file))
        return ancestor

    def enrich(self) -> list[Node]:
        """Return a new list of Nodes with health metrics merged into `metadata`."""
        fan_in, fan_out = self._compute_fan_counts()
        depths = self._compute_depths()
        cyclic_file_ids = self._compute_cycles()
        file_ancestor = self._compute_file_ancestors()

        result: list[Node] = []
        for node in self._nodes:
            ancestor_file = file_ancestor.get(node.id, node.id)
            in_cycle = ancestor_file in cyclic_file_ids
            result.append(
                node.model_copy(
                    update={
                        "metadata": {
                            **node.metadata,
                            "fan_in": fan_in.get(node.id, 0),
                            "fan_out": fan_out.get(node.id, 0),
                            "depth": depths.get(node.id, 0),
                            "in_cycle": in_cycle,
                        }
                    }
                )
            )
        return result
