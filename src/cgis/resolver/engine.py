"""Implements ResolverEngine class."""

from cgis.core.models import (
    RAW_CLASS_PREFIX,
    SELF_PREFIX,
    VIRTUAL_FILE_PATH,
    Edge,
    EdgeType,
    Node,
    NodeNamespace,
    NodeType,
)
from cgis.resolver.symbols import SymbolResolver

RAW_DEP_PREFIX = "raw_dep:"
RAW_IMPORT_PREFIX = "raw_import:"


class ResolverEngine:
    """
    The 'Brain' of the CGIS.
    Transforms 'raw' semantic edges into resolved, high-confidence edges.

    Thin facade: SymbolResolver builds the SymbolIndex internally (via
    IndexBuilder), maps raw names to FQNs, and this class keeps edge
    finalization — confidence policy, edge rewrites, and virtual-node
    creation (spec §2.5).
    """

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Build the symbol resolver (which builds the index) from the extracted graph."""
        self.edges = edges
        self._resolver = SymbolResolver(nodes, edges)
        self._index = self._resolver.index

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
            if edge.target.startswith(RAW_CLASS_PREFIX):
                class_edge = self._resolved_class_edge(edge)
                resolved_edges.append(class_edge)
                self._ensure_virtual_node(class_edge.target, virtual_nodes)
            elif edge.target.startswith(RAW_DEP_PREFIX):
                dep_edge = self._resolved_dep_edge(edge)
                if dep_edge is not None:
                    resolved_edges.append(dep_edge)
            elif edge.target.startswith(RAW_IMPORT_PREFIX):
                import_edge = self._resolved_import_edge(edge)
                if import_edge is not None:
                    resolved_edges.append(import_edge)
                # no _ensure_virtual_node: target exists on hit, edge dies on miss
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
        raw = edge.target.removeprefix(RAW_CLASS_PREFIX)
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
        if raw_name.startswith(SELF_PREFIX):
            new_target = self._resolver.resolve_self_call(
                edge.source, raw_name.removeprefix(SELF_PREFIX)
            )
        else:
            new_target = self._resolver.resolve_global_call(raw_name, edge.source, edge.file_path)
        final_target = new_target or raw_name
        confidence = min(edge.confidence + 0.5, 1.0) if new_target else 0.8
        return edge.model_copy(update={"target": final_target, "confidence": confidence})

    def _resolved_dep_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_dep: candidate edge, or None when it must be dropped (spec §3.3).

        A candidate resolves in one of two ways: to a DI alias (a VARIABLE node),
        which keeps it a DEPENDS_ON wiring edge, or to an internal class, which
        makes it a REFERENCES annotation edge (spec D4). Anything else is a
        speculative candidate that must not leak into the output.

        The internal-class check is inlined here rather than delegated to
        SymbolResolver: resolution (resolve_class_ref, already public and
        already called from this class) belongs to the symbol layer, but "is
        this FQN an existing CLASS node" is edge-finalization policy — this
        class's stated job — not a resolution strategy. resolve_class_ref can
        return an import-map FQN for a symbol with no node (a third-party
        type via its `... or target_fqn` fallback); the membership check
        below is what keeps that out (spec D3).

        A candidate whose source is the target class itself, or lives inside
        it (a method, or a nested class), is also dropped: a class naming
        itself is not evidence anyone uses it, and counting it would
        manufacture a false negative in the orphan query (spec D9).
        """
        dep_name = edge.target.removeprefix(RAW_DEP_PREFIX)
        dep_target = self._resolver.resolve_dep_candidate(dep_name, edge.source, edge.file_path)
        if dep_target is not None:
            return edge.model_copy(update={"target": dep_target, "confidence": 1.0})
        resolved = self._resolver.resolve_class_ref(dep_name, edge.source, edge.file_path)
        if resolved is not None:
            class_node = self._index.nodes.get(resolved)
            if class_node is not None and class_node.type == NodeType.CLASS:
                if edge.source == resolved or edge.source.startswith(f"{resolved}."):
                    return None
                return edge.model_copy(
                    update={"target": resolved, "type": EdgeType.REFERENCES, "confidence": 1.0}
                )
        return None

    def _resolved_import_edge(self, edge: Edge) -> Edge | None:
        """Resolve a raw_import: symbol edge, or None when it must be dropped.

        Reuses SymbolIndex.map_to_node_fqn (exact / suffix / strip-prefix). An
        external or unknown symbol drops the edge: the module-level IMPORTS
        edge already captures the coupling — raw_import: never leaks into
        output and never mints a virtual node (spec §2.2/§2.4).
        """
        imported_fqn = edge.target.removeprefix(RAW_IMPORT_PREFIX)
        node_fqn = self._index.map_to_node_fqn(imported_fqn)
        if node_fqn is None:
            return None
        return edge.model_copy(update={"target": node_fqn, "confidence": 1.0})

    def _ensure_virtual_node(self, target: str, virtual_nodes: dict[str, Node]) -> None:
        """Create a virtual boundary node for target if it is not already in the graph."""
        if not self._index.has_node(target) and target not in virtual_nodes:
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
