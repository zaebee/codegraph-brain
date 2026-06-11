"""Implements ResolverEngine class."""

from cgis.core.models import VIRTUAL_FILE_PATH, Edge, Node, NodeNamespace, NodeType
from cgis.resolver.symbols import _RAW_CLASS_PREFIX, _SELF_PREFIX, IndexBuilder, SymbolResolver

RAW_DEP_PREFIX = "raw_dep:"


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.

    Thin facade: IndexBuilder builds the SymbolIndex, SymbolResolver maps raw
    names to FQNs, and this class keeps edge finalization — confidence policy,
    edge rewrites, and virtual-node creation (spec §2.5).
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Build the symbol index and resolver from the extracted graph."""
        self.edges = edges
        self._index = IndexBuilder().build(nodes)
        self._resolver = SymbolResolver(self._index, edges)

    def resolve(self) -> tuple[list[Edge], list[Node]]:
        """
        Phase 3: The Linking Pass.
        Resolves raw_call targets to FQNs in a single pass. Virtual nodes for
        boundary symbols (STDLIB/EXTERNAL/UNKNOWN) are created on the fly as
        each edge target is finalized.

        Returns (resolved_edges, virtual_nodes).
        """
        resolved_edges: list[Edge] = []
        virtual_nodes: dict[str, Node] = {}

        for edge in self.edges:
            if edge.target.startswith(_RAW_CLASS_PREFIX):
                class_edge = self._resolved_class_edge(edge)
                resolved_edges.append(class_edge)
                self._ensure_virtual_node(class_edge.target, virtual_nodes)
            elif edge.target.startswith(RAW_DEP_PREFIX):
                dep_edge = self._resolved_dep_edge(edge)
                if dep_edge is not None:
                    resolved_edges.append(dep_edge)
            elif not edge.target.startswith("raw_call:"):
                resolved_edges.append(edge)
                self._ensure_virtual_node(edge.target, virtual_nodes)
            else:
                call_edge = self._resolved_call_edge(edge)
                resolved_edges.append(call_edge)
                self._ensure_virtual_node(call_edge.target, virtual_nodes)

        return resolved_edges, list(virtual_nodes.values())

    def _resolved_class_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_class: edge to its final class FQN.

        Strips the raw_class: prefix, resolves via SymbolResolver, then
        returns a copy of the edge with the resolved target (confidence 1.0) or
        the bare name as fallback (confidence 0.5).
        """
        raw = edge.target.removeprefix(_RAW_CLASS_PREFIX)
        resolved = self._resolver.resolve_class_ref(raw, edge.source, edge.file_path)
        final_target = resolved or raw
        confidence = 1.0 if resolved else 0.5
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_call_edge(self, edge: Edge) -> Edge:
        """Resolve a raw_call: edge to its final call target FQN.

        Strips the raw_call: prefix, dispatches to resolve_self_call for
        self.* calls or resolve_global_call otherwise, then returns a copy of
        the edge with the resolved target and adjusted confidence:
        min(edge.confidence + 0.5, 1.0) on success, 0.8 on failure.
        """
        raw_name = edge.target.removeprefix("raw_call:")
        if raw_name.startswith(_SELF_PREFIX):
            new_target = self._resolver.resolve_self_call(
                edge.source, raw_name.removeprefix(_SELF_PREFIX)
            )
        else:
            new_target = self._resolver.resolve_global_call(raw_name, edge.source, edge.file_path)
        final_target = new_target or raw_name
        confidence = min(edge.confidence + 0.5, 1.0) if new_target else 0.8
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3)."""
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolver.resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is None:
            # Speculative candidate that is not a DI alias: drop the edge
            # entirely — raw_dep: must never leak into output (spec §3.3).
            return None
        return edge.model_copy(update={"target": dep_target, "confidence": 1.0})

    def _ensure_virtual_node(self, target: str, virtual_nodes: dict[str, Node]) -> None:
        """Create a virtual boundary node for target if it is not already in the graph."""
        if target not in self._index.nodes and target not in virtual_nodes:
            virtual_nodes[target] = self._make_virtual_node(
                target, self._index.classify_fqn(target)
            )

    def _make_virtual_node(self, fqn: str, namespace: NodeNamespace) -> Node:
        """Create a placeholder node for an external/stdlib symbol."""
        return Node(
            id=fqn,
            type=NodeType.FUNCTION,
            name=fqn.rsplit(".", maxsplit=1)[-1],
            file_path=VIRTUAL_FILE_PATH,
            start_line=0,
            end_line=0,
            namespace=namespace,
            confidence_score=0.8,
        )
